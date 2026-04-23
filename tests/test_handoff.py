import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from ai_cli.main import (
    _claim_handoff_for_signal,
    _find_best_handoff,
    _log_handoff_event,
    check_handoff,
    check_handoff_project,
    claim_handoff,
    cli,
    complete_handoff,
    post_handoff,
    _cmd_signal_watch_start,
    _cmd_signal_watch_stop,
    _cmd_signal_watch_status,
    _ensure_circusd,
)


# --- Handoff subcommands ---


class TestHandoff:
    def test_post_handoff_when_called_then_creates_file(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            post_handoff("Fix bug", "P1", "myapp", "Details here", for_machine="hetzner")
        pending_files = list((queue_dir / "pending").glob("*.md"))
        assert len(pending_files) == 1
        content = pending_files[0].read_text()
        assert "Fix bug" in content
        assert "Details here" in content

    def test_post_handoff_publishes_to_nats_with_correct_subject(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        mock_client = MagicMock()
        mock_client.publish = AsyncMock(return_value=True)

        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            with patch("ai_cli.config.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
                    post_handoff("Deploy done", "P1", "artelier", "Details", for_machine="hetzner")

        mock_client.publish.assert_called_once()
        subject, payload = mock_client.publish.call_args[0]
        assert subject == "handoff.artelier"
        assert payload["title"] == "Deploy done"
        assert payload["project"] == "artelier"
        assert payload["priority"] == "P1"

    def test_post_handoff_when_nats_fails_then_file_still_written(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            with patch("ai_cli.config.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient", side_effect=Exception("NATS unavailable")):
                    post_handoff("Fix bug", "P1", "myapp", "Details", for_machine="hetzner")
        pending_files = list((queue_dir / "pending").glob("*.md"))
        assert len(pending_files) == 1

    def test_post_handoff_when_no_main_project_then_exits(self):
        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=None):
            with pytest.raises(SystemExit) as exc:
                post_handoff("title", "P1", "proj", "msg", for_machine="hetzner")
            assert exc.value.code == 1

    def test_post_handoff_when_no_for_machine_then_exits(self):
        with pytest.raises(SystemExit) as exc:
            post_handoff("title", "P1", "proj", "msg")
        assert exc.value.code == 1

    def test_post_handoff_writes_explicit_for_machine(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            post_handoff("Task", "P1", "proj", "msg", for_machine="mac")
        content = list((queue_dir / "pending").glob("*.md"))[0].read_text()
        assert "for_machine: mac" in content

    def test_post_handoff_includes_for_machine_in_nats_payload(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        mock_client = MagicMock()
        mock_client.publish = AsyncMock(return_value=True)
        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            with patch("ai_cli.config.load_config", return_value={}):
                with patch("ai_cli.messaging.NATSClient", return_value=mock_client):
                    post_handoff("Task", "P1", "proj", "msg", for_machine="mac")
        _, payload = mock_client.publish.call_args[0]
        assert payload["for_machine"] == "mac"

    def test_check_handoff_when_pending_items_then_prints_best(self, tmp_path, capsys):
        queue_dir = tmp_path / ".handoff-queue"
        pending = queue_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "001-task-a.md").write_text("---\npriority: 2\n---\nTask A")
        (pending / "002-task-b.md").write_text("---\npriority: 0\n---\nTask B")

        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            check_handoff()
        output = capsys.readouterr().out
        assert "002-task-b.md" in output

    def test_check_handoff_when_no_pending_then_reports_empty(self, tmp_path, capsys):
        queue_dir = tmp_path / ".handoff-queue"
        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            check_handoff()
        assert "No pending handoffs" in capsys.readouterr().out

    def test_claim_handoff_when_file_exists_then_moves_to_claimed(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        pending = queue_dir / "pending"
        pending.mkdir(parents=True)
        src = pending / "001-task.md"
        src.write_text("---\nclaimed_by: null\nclaimed_at: null\n---\nContent")

        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            claim_handoff(str(src), claimer="c-sw-1")
        claimed_files = list((queue_dir / "claimed").glob("*.md"))
        assert len(claimed_files) == 1
        content = claimed_files[0].read_text()
        assert "c-sw-1" in content

    def test_claim_handoff_when_no_main_project_then_exits(self):
        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=None):
            with pytest.raises(SystemExit) as exc:
                claim_handoff("/tmp/nonexistent.md")
            assert exc.value.code == 1

    def test_complete_handoff_when_file_exists_then_moves_to_completed(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        claimed = queue_dir / "claimed"
        claimed.mkdir(parents=True)
        src = claimed / "001-task.md"
        src.write_text("---\ntitle: task\n---\n")

        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            complete_handoff(str(src))
        completed = list((queue_dir / "completed").glob("*.md"))
        assert len(completed) == 1

    def test_complete_handoff_when_no_main_project_then_exits(self):
        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=None):
            with pytest.raises(SystemExit) as exc:
                complete_handoff("/tmp/nonexistent.md")
            assert exc.value.code == 1


# --- _claim_handoff_for_signal ---


class TestClaimHandoffForSignal:
    def _make_pending(self, handoff_dir, handoff_id, title, claimer=None):
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True, exist_ok=True)
        slug = title.lower().replace(" ", "-")
        f = pending / f"{handoff_id:03d}-{slug}.md"
        f.write_text(f'---\nid: "{handoff_id}"\ntitle: "{title}"\nclaimed_by: null\nclaimed_at: null\n---\n')
        return f

    def test_when_pending_file_exists_then_moves_to_claimed(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        self._make_pending(handoff_dir, 1, "fix-bug")

        result = _claim_handoff_for_signal(handoff_dir, 1, "c-sw-1")

        assert result is not None
        assert result.parent.name == "claimed"
        assert result.name == "001-fix-bug.md"
        assert not (handoff_dir / "pending" / "001-fix-bug.md").exists()

    def test_when_pending_file_exists_then_updates_claimed_by(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        self._make_pending(handoff_dir, 2, "deploy")

        result = _claim_handoff_for_signal(handoff_dir, 2, "c-sw-2")

        assert result is not None
        text = result.read_text()
        assert "claimed_by: c-sw-2" in text
        assert "claimed_at: null" not in text

    def test_when_no_pending_file_then_returns_none(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        (handoff_dir / "pending").mkdir(parents=True)

        result = _claim_handoff_for_signal(handoff_dir, 99, "c-sw-1")

        assert result is None

    def test_when_file_already_claimed_by_other_session_then_returns_none(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        f = self._make_pending(handoff_dir, 1, "task")
        claimed = handoff_dir / "claimed"
        claimed.mkdir(parents=True)
        f.rename(claimed / f.name)

        result = _claim_handoff_for_signal(handoff_dir, 1, "c-sw-1")

        assert result is None

    def test_when_rename_raises_oserror_then_returns_none(self, tmp_path):
        """Covers the concurrent-rename race: another session's mv wins, ours gets OSError."""
        handoff_dir = tmp_path / ".handoff-queue"
        self._make_pending(handoff_dir, 1, "task")

        with patch("pathlib.Path.rename", side_effect=OSError("busy")):
            result = _claim_handoff_for_signal(handoff_dir, 1, "c-sw-1")

        assert result is None


# --- ai internal signal-watch CLI dispatch ---


class TestSignalWatchCli:
    def test_signal_watch_when_nats_unavailable_then_exits_cleanly(self):
        from nats.errors import NoServersError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-1"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=None),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=None),
            patch("ai_cli.config.get_xdg_state_home", return_value=Path("/tmp")),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=Path("/tmp")),
            patch("nats.connect", new=AsyncMock(side_effect=NoServersError)),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_signal_watch_when_message_received_and_claim_succeeds_then_writes_pending_file(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "003-fix-login.md").write_text(
            '---\nid: "3"\ntitle: "fix login"\nfor_machine: hetzner\nclaimed_by: null\nclaimed_at: null\n---\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = (
                    b'{"id": 3, "title": "fix login", "priority": "P1", "message": "do it", "for_machine": "hetzner"}'
                )
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-1"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        pending_file = state_dir / "handoff-pending-c-sw-1"
        assert pending_file.exists()
        content = pending_file.read_text()
        assert "fix login" in content
        assert not (pending / "003-fix-login.md").exists()

    def test_signal_watch_when_claim_succeeds_then_touches_signal_file(self, tmp_path):
        """After claiming a handoff, signal-watch must touch cc-exit-{session} to wake the watcher."""
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "003-wake-test.md").write_text(
            '---\nid: "3"\ntitle: "wake test"\nfor_machine: hetzner\nclaimed_by: null\nclaimed_at: null\n---\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = (
                    b'{"id": 3, "title": "wake test", "priority": "P1", "message": "body", "for_machine": "hetzner"}'
                )
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-1"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        # Watcher signal_file must be touched so the watcher injects /exit
        assert (state_dir / "cc-exit-c-sw-1").exists()

    def test_signal_watch_when_claim_lost_to_other_session_then_no_pending_file(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        (handoff_dir / "pending").mkdir(parents=True)
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = b'{"id": 5, "title": "gone", "priority": "P2", "message": "already claimed"}'
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-2"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert not (state_dir / "handoff-pending-c-sw-2").exists()

    def test_signal_watch_when_too_few_args_then_exits_with_error(self):
        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "only-one-arg"]),
            patch("ai_cli.config.load_config", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 1

    def test_signal_watch_when_handoff_dir_none_and_message_received_then_no_pending_file(self, tmp_path):
        """Covers the early-return branch when handoff_dir is None but a message is delivered."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = b'{"id": 7, "title": "t", "priority": "P1", "message": "m"}'
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-3"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=None),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=None),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert not (state_dir / "handoff-pending-c-sw-3").exists()

    def test_signal_watch_when_realtime_delivery_then_never_sends_nudge(self, tmp_path):
        """signal-watch must never call tmux send-keys."""
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        file_content = (
            '---\nid: "4"\ntitle: "nudge task"\nfor_machine: hetzner\nclaimed_by: null\nclaimed_at: null\n---\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe
        subprocess_calls = []

        def fake_subprocess_run(cmd, **kwargs):
            subprocess_calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = "bash\n"
            return result

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = json.dumps(
                    {
                        "id": 4,
                        "title": "nudge task",
                        "priority": "P1",
                        "message": "do it",
                        "for_machine": "hetzner",
                        "content": file_content,
                        "filename": "004-nudge-task.md",
                    }
                ).encode()
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-4"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run", side_effect=fake_subprocess_run),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert not any(
            "send-keys" in str(cmd) for cmd in subprocess_calls
        ), "send-keys must never be called — pending file is the pickup mechanism"

    def test_signal_watch_when_cross_machine_payload_then_writes_file_and_claims(self, tmp_path):
        """File doesn't exist locally; content in NATS payload should be written before claiming."""
        handoff_dir = tmp_path / ".handoff-queue"
        (handoff_dir / "pending").mkdir(parents=True)
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe

        file_content = '---\nid: "7"\ntitle: "remote task"\nfor_machine: hetzner\nclaimed_by: null\nclaimed_at: null\n---\n\nDo this.\n'

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = json.dumps(
                    {
                        "id": 7,
                        "title": "remote task",
                        "priority": "P1",
                        "message": "Do this.",
                        "for_machine": "hetzner",
                        "content": file_content,
                        "filename": "007-remote-task.md",
                    }
                ).encode()
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-3"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        pending_file = state_dir / "handoff-pending-c-sw-3"
        assert pending_file.exists()
        assert "remote task" in pending_file.read_text()
        assert not (handoff_dir / "pending" / "007-remote-task.md").exists()
        assert (handoff_dir / "claimed" / "007-remote-task.md").exists()

    def test_signal_watch_startup_scan_picks_up_existing_pending_files(self, tmp_path):
        """Files already in pending queue at startup should be claimed without a NATS trigger."""
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "004-pre-existing-task.md").write_text(
            '---\nid: "4"\ntitle: "pre-existing task"\nfor_machine: hetzner\nclaimed_by: null\nclaimed_at: null\n---\n\nDo this.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js

        async def fake_js_subscribe(subject, durable, cb):
            pass

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-4"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        pending_file = state_dir / "handoff-pending-c-sw-4"
        assert pending_file.exists()
        assert not (pending / "004-pre-existing-task.md").exists()
        assert (handoff_dir / "claimed" / "004-pre-existing-task.md").exists()

    def test_signal_watch_startup_scan_when_claim_succeeds_then_touches_signal_file(self, tmp_path):
        """Startup scan must also touch cc-exit-{session} to wake a running session."""
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "004-startup-wake.md").write_text(
            '---\nid: "4"\ntitle: "startup wake"\nfor_machine: hetzner\nclaimed_by: null\nclaimed_at: null\n---\n\nDo this.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js

        async def fake_js_subscribe(subject, durable, cb):
            pass

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-4"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert (state_dir / "cc-exit-c-sw-4").exists()

    def test_signal_watch_startup_scan_does_not_send_keys(self, tmp_path):
        """Startup scan must never send-keys."""
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "005-startup-task.md").write_text(
            '---\nid: "5"\ntitle: "startup task"\nfor_machine: hetzner\nclaimed_by: null\nclaimed_at: null\n---\n\nDo this.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js

        async def fake_js_subscribe(subject, durable, cb):
            pass

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            raise asyncio.CancelledError

        send_keys_calls = []

        def fake_subprocess_run(cmd, **kwargs):
            if "send-keys" in cmd:
                send_keys_calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = "bash\n"
            return result

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-5"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run", side_effect=fake_subprocess_run),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert (state_dir / "handoff-pending-c-sw-5").exists()
        assert send_keys_calls == [], "startup scan must not send-keys"

    def test_signal_watch_when_for_machine_matches_then_claims(self, tmp_path):
        """Handoff with for_machine matching AI_CLI_HOST should be claimed."""
        handoff_dir = tmp_path / ".handoff-queue"
        (handoff_dir / "pending").mkdir(parents=True)
        file_content = '---\nid: "8"\ntitle: "local task"\nclaimed_by: null\nclaimed_at: null\n---\n'
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = json.dumps(
                    {
                        "id": 8,
                        "title": "local task",
                        "priority": "P1",
                        "message": "do it",
                        "for_machine": "hetzner",
                        "content": file_content,
                        "filename": "008-local-task.md",
                    }
                ).encode()
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-6"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert (state_dir / "handoff-pending-c-sw-6").exists()

    def test_signal_watch_when_for_machine_mismatch_then_skips(self, tmp_path):
        """Handoff with for_machine not matching AI_CLI_HOST must be ignored."""
        handoff_dir = tmp_path / ".handoff-queue"
        (handoff_dir / "pending").mkdir(parents=True)
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = json.dumps(
                    {
                        "id": 9,
                        "title": "mac only task",
                        "priority": "P1",
                        "message": "mac work",
                        "for_machine": "mac",
                    }
                ).encode()
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-7"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert not (state_dir / "handoff-pending-c-sw-7").exists()

    def test_signal_watch_startup_scan_when_for_machine_mismatch_then_skips(self, tmp_path):
        """Startup scan must skip files whose for_machine doesn't match AI_CLI_HOST."""
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "010-mac-task.md").write_text(
            '---\nid: "10"\ntitle: "mac task"\nfor_machine: mac\nclaimed_by: null\nclaimed_at: null\n---\n\nMac only.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js

        async def fake_js_subscribe(subject, durable, cb):
            pass

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-8"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert (pending / "010-mac-task.md").exists()
        assert not (state_dir / "handoff-pending-c-sw-8").exists()

    def test_signal_watch_startup_scan_when_no_for_machine_then_skips(self, tmp_path):
        """Startup scan must skip files with empty/missing for_machine (required field)."""
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "011-unaddressed.md").write_text(
            '---\nid: "11"\ntitle: "unaddressed"\nclaimed_by: null\nclaimed_at: null\n---\n\nNo machine set.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js

        async def fake_js_subscribe(subject, durable, cb):
            pass

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-9"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert not (state_dir / "handoff-pending-c-sw-9").exists()

    def test_signal_watch_when_nats_delivers_already_claimed_file_then_skips(self, tmp_path):
        """NATS re-delivers a message for a handoff already in claimed/ — must not re-dispatch."""
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        claimed = handoff_dir / "claimed"
        pending.mkdir(parents=True)
        claimed.mkdir(parents=True)
        file_content = (
            '---\nid: "4"\ntitle: "test-drain-4"\nfor_machine: mac\n'
            'claimed_by: "c-ai-cli-2"\nclaimed_at: "2026-04-01T21:23:40Z"\n---\n\nAlready done.\n'
        )
        (claimed / "004-test-drain-4.md").write_text(file_content)
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = json.dumps(
                    {
                        "id": 4,
                        "title": "test-drain-4",
                        "priority": "P1",
                        "message": "Already done.",
                        "for_machine": "mac",
                        "content": file_content,
                        "filename": "004-test-drain-4.md",
                    }
                ).encode()
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "ai-cli-utils", "c-mobile-1"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
            patch.dict("os.environ", {"AI_CLI_HOST": "mac"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert not (pending / "004-test-drain-4.md").exists()
        assert not (state_dir / "handoff-pending-c-mobile-1").exists()


# --- ai handoff post --remote ---


class TestHandoffPostRemote:
    def test_post_handoff_remote_flag_when_no_remote_config_then_exits_1(self):
        with (
            patch("sys.argv", ["ai", "handoff", "post", "--remote", "Fix bug", "P1", "myapp", "Details"]),
            patch("ai_cli.config.load_config", return_value={}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
        assert exc.value.code == 1

    def test_post_handoff_remote_flag_when_remote_config_set_then_sshs_to_configured_host(self):
        config = {"remote": {"host": "9.9.9.9", "user": "alice"}}
        with (
            patch("sys.argv", ["ai", "handoff", "post", "--remote", "Task", "P2", "proj", "Msg"]),
            patch("ai_cli.config.load_config", return_value=config),
            patch("os.execvp", side_effect=SystemExit(0)) as mock_exec,
        ):
            with pytest.raises(SystemExit):
                cli()

        mock_exec.assert_called_once()
        cmd, args = mock_exec.call_args[0]
        assert cmd == "ssh"
        assert "alice@9.9.9.9" in args

    def test_post_handoff_without_remote_flag_writes_local_file(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            post_handoff("Local task", "P1", "myapp", "Details", for_machine="hetzner")
        files = list((queue_dir / "pending").glob("*.md"))
        assert len(files) == 1

    def test_post_handoff_nats_payload_includes_content_and_filename(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        published_payloads = []

        class FakeNATSClient:
            def __init__(self, servers=None):
                pass

            async def publish(self, subject, data):
                published_payloads.append((subject, data))
                return True

        import ai_cli.messaging as msg_mod

        with (
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir),
            patch("ai_cli.config.load_config", return_value={}),
            patch.object(msg_mod, "NATSClient", FakeNATSClient),
        ):
            post_handoff("Cross-machine task", "P1", "myapp", "Do this remotely", for_machine="hetzner")

        assert len(published_payloads) == 1
        subject, payload = published_payloads[0]
        assert "content" in payload
        assert "filename" in payload
        assert "Cross-machine task" in payload["content"]
        assert payload["filename"].endswith(".md")


# --- post_handoff ID scanning ---


class TestPostHandoffIdScanning:
    def test_post_handoff_when_existing_files_with_bad_names_then_handles_valueerror(self, tmp_path):
        """Covers lines 740-746: ValueError parsing file IDs."""
        queue_dir = tmp_path / ".handoff-queue"
        pending = queue_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "bad-name.md").write_text("content")

        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            post_handoff("New task", "P1", "proj", "msg", for_machine="hetzner")
        files = list(pending.glob("001-*.md"))
        assert len(files) == 1


class TestPostHandoffExistingFilesMultiDir:
    def test_post_handoff_when_existing_in_claimed_and_completed_then_scans_all(self, tmp_path):
        """Covers lines 743-744: scanning across pending/claimed/completed for max ID."""
        queue_dir = tmp_path / ".handoff-queue"
        claimed = queue_dir / "claimed"
        claimed.mkdir(parents=True)
        (claimed / "005-old-task.md").write_text("content")
        completed = queue_dir / "completed"
        completed.mkdir(parents=True)
        (completed / "010-done-task.md").write_text("content")

        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            post_handoff("New task", "P1", "proj", "msg", for_machine="hetzner")
        pending_files = list((queue_dir / "pending").glob("*.md"))
        assert len(pending_files) == 1
        assert "011-" in pending_files[0].name


# --- check_handoff edge cases ---


class TestCheckHandoffEdgeCases:
    def test_check_handoff_when_no_handoff_dir_then_reports_empty(self, capsys):
        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=None):
            check_handoff()
        assert "No pending handoffs" in capsys.readouterr().out

    def test_check_handoff_when_bad_priority_then_skips(self, tmp_path, capsys):
        """Covers lines 770-771: ValueError parsing priority."""
        queue_dir = tmp_path / ".handoff-queue"
        pending = queue_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "001-task.md").write_text("---\npriority: notanumber\n---\nTask")

        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            check_handoff()
        output = capsys.readouterr().out
        assert "001-task.md" in output

    def test_check_handoff_when_equal_priority_and_first_is_none_then_picks_first(self, tmp_path, capsys):
        """Covers lines 775-776: equal priority, best_file is None initially."""
        queue_dir = tmp_path / ".handoff-queue"
        pending = queue_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "001-task.md").write_text("---\npriority: 5\n---\nTask A")

        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            check_handoff()
        output = capsys.readouterr().out
        assert "001-task.md" in output


class TestClaimHandoffException:
    def test_claim_handoff_when_rename_fails_then_exits(self, tmp_path):
        """Covers lines 793-794: exception during file rename."""
        queue_dir = tmp_path / ".handoff-queue"
        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            with pytest.raises(SystemExit) as exc:
                claim_handoff("/nonexistent/path.md")
            assert exc.value.code == 1


class TestCompleteHandoffException:
    def test_complete_handoff_when_source_missing_then_exits(self, tmp_path):
        queue_dir = tmp_path / ".handoff-queue"
        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=queue_dir):
            with pytest.raises(SystemExit) as exc:
                complete_handoff("/nonexistent/path.md")
            assert exc.value.code == 1


# --- _find_best_handoff ---


class TestFindBestHandoff:
    def test_find_best_handoff_when_no_filter_returns_highest_priority(self, tmp_path):
        queue_dir = tmp_path / "pending"
        queue_dir.mkdir()
        (queue_dir / "001-low.md").write_text("---\npriority: P3\nproject: app\n---\n")
        (queue_dir / "002-high.md").write_text("---\npriority: P1\nproject: other\n---\n")
        result = _find_best_handoff(queue_dir)
        assert result is not None
        assert result.name == "002-high.md"

    def test_find_best_handoff_when_project_filter_returns_only_matching(self, tmp_path):
        queue_dir = tmp_path / "pending"
        queue_dir.mkdir()
        (queue_dir / "001-other.md").write_text("---\npriority: P0\nproject: other\n---\n")
        (queue_dir / "002-mine.md").write_text("---\npriority: P2\nproject: myapp\n---\n")
        result = _find_best_handoff(queue_dir, project_filter="myapp")
        assert result is not None
        assert result.name == "002-mine.md"

    def test_find_best_handoff_when_no_match_for_project_returns_none(self, tmp_path):
        queue_dir = tmp_path / "pending"
        queue_dir.mkdir()
        (queue_dir / "001-other.md").write_text("---\npriority: P1\nproject: other\n---\n")
        result = _find_best_handoff(queue_dir, project_filter="myapp")
        assert result is None

    def test_find_best_handoff_when_dir_missing_returns_none(self, tmp_path):
        result = _find_best_handoff(tmp_path / "nonexistent")
        assert result is None


# --- check_handoff_project ---


class TestCheckHandoffProject:
    def test_check_handoff_project_when_match_then_prints_path(self, tmp_path, capsys):
        handoff_dir = tmp_path / ".handoff-queue"
        queue_dir = handoff_dir / "pending"
        queue_dir.mkdir(parents=True)
        (queue_dir / "001-task.md").write_text("---\npriority: P1\nproject: myapp\n---\n")
        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir):
            check_handoff_project("myapp")
        out = capsys.readouterr().out.strip()
        assert "001-task.md" in out

    def test_check_handoff_project_when_no_match_then_reports_empty(self, tmp_path, capsys):
        handoff_dir = tmp_path / ".handoff-queue"
        queue_dir = handoff_dir / "pending"
        queue_dir.mkdir(parents=True)
        (queue_dir / "001-task.md").write_text("---\npriority: P1\nproject: other\n---\n")
        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir):
            check_handoff_project("myapp")
        assert "No pending handoffs" in capsys.readouterr().out

    def test_check_handoff_project_when_no_handoff_dir_then_reports_empty(self, capsys):
        with patch("ai_cli.handoff._get_handoff_queue_dir", return_value=None):
            check_handoff_project("myapp")
        assert "No pending handoffs" in capsys.readouterr().out


# --- _log_handoff_event ---


class TestLogHandoffEvent:
    def test_log_event_when_called_then_writes_jsonl(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        with patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir):
            _log_handoff_event("handoff.posted", handoff_id=1, project="app")
        log_file = state_dir / "handoff-events.jsonl"
        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["event"] == "handoff.posted"
        assert entry["handoff_id"] == 1
        assert "ts" in entry

    def test_log_event_when_dir_missing_then_creates_it(self, tmp_path):
        state_dir = tmp_path / "deep" / "state"
        with patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir):
            _log_handoff_event("handoff.claimed", session="c-sw-1")
        assert (state_dir / "handoff-events.jsonl").exists()

    def test_log_event_appends_multiple(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        with patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir):
            _log_handoff_event("handoff.posted", handoff_id=1)
            _log_handoff_event("handoff.claimed", handoff_id=1)
        lines = (state_dir / "handoff-events.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2


# --- post_handoff event logging ---


class TestPostHandoffEventLogging:
    def test_post_handoff_logs_event(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        with (
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch.dict(os.environ, {"AI_TMUX_SESSION": "c-sw-1"}),
        ):
            post_handoff("Test task", "P1", "myapp", "Do this thing", for_machine="hetzner")
        log_file = state_dir / "handoff-events.jsonl"
        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["event"] == "handoff.posted"
        assert entry["title"] == "Test task"


# --- signal-watch event logging ---


class TestSignalWatchEventLogging:
    def test_signal_watch_when_claim_succeeds_then_logs_claimed_event(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe
        file_content = (
            '---\nid: "5"\ntitle: "test task"\nfor_machine: hetzner\nclaimed_by: null\nclaimed_at: null\n---\n'
        )

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = json.dumps(
                    {
                        "id": 5,
                        "title": "test task",
                        "priority": "P1",
                        "message": "do it",
                        "for_machine": "hetzner",
                        "content": file_content,
                        "filename": "005-test-task.md",
                    }
                ).encode()
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-5"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        log_file = state_dir / "handoff-events.jsonl"
        assert log_file.exists()
        events = [json.loads(l) for l in log_file.read_text().strip().split("\n")]
        claimed = [e for e in events if e["event"] == "handoff.claimed"]
        assert len(claimed) == 1
        assert claimed[0]["handoff_id"] == 5
        assert claimed[0]["layer"] == "nats_realtime"

    def test_signal_watch_when_claim_succeeds_then_no_send_keys_nudge(self, tmp_path):
        """Realtime NATS delivery: pending file written, no send-keys nudge."""
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        file_content = (
            '---\nid: "6"\ntitle: "nudge me"\nfor_machine: hetzner\nclaimed_by: null\nclaimed_at: null\n---\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe
        subprocess_calls = []

        def fake_subprocess_run(cmd, **kwargs):
            subprocess_calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = "bash\n"
            return result

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = json.dumps(
                    {
                        "id": 6,
                        "title": "nudge me",
                        "priority": "P1",
                        "message": "do it",
                        "for_machine": "hetzner",
                        "content": file_content,
                        "filename": "006-nudge-task.md",
                    }
                ).encode()
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-6"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run", side_effect=fake_subprocess_run),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert (state_dir / "handoff-pending-c-sw-6").exists()
        assert not any("send-keys" in str(cmd) for cmd in subprocess_calls)
        log_file = state_dir / "handoff-events.jsonl"
        events = [json.loads(l) for l in log_file.read_text().strip().split("\n")]
        assert not any(e["event"] == "handoff.nudge_sent" for e in events)

    def test_signal_watch_startup_scan_logs_as_startup_scan_layer(self, tmp_path):
        """Startup scan claims should be logged with layer=startup_scan."""
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "007-scan-task.md").write_text(
            '---\nid: "7"\ntitle: "scan task"\npriority: P2\nfor_machine: hetzner\nclaimed_by: null\nclaimed_at: null\n---\n\nDo this.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="handoff")
        mock_nc.jetstream.return_value = mock_js

        async def fake_js_subscribe(subject, durable, cb):
            pass

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "signal-watch", "myapp", "c-sw-7"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=fake_sleep),
            patch("subprocess.run"),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        log_file = state_dir / "handoff-events.jsonl"
        events = [json.loads(l) for l in log_file.read_text().strip().split("\n")]
        claimed = [e for e in events if e["event"] == "handoff.claimed"]
        assert len(claimed) == 1
        assert claimed[0]["layer"] == "startup_scan"


# --- engine script handoff pickup ---


class TestEngineScriptStartupHandoffPickup:
    def test_get_engine_script_includes_startup_handoff_drain(self):
        from ai_cli.main import get_engine_script

        script = get_engine_script(
            "c",
            "ai-cli-2",
            "c-ai-cli-2",
            "c-ai-cli-",
            "ai",
            project_name="ai-cli-utils",
        )
        assert "ai internal handoff-drain" in script

    def test_get_engine_script_startup_drain_only_on_first_run(self):
        from ai_cli.main import get_engine_script

        script = get_engine_script(
            "c",
            "ai-cli-2",
            "c-ai-cli-2",
            "c-ai-cli-",
            "ai",
            project_name="ai-cli-utils",
        )
        assert "$first_run" in script

    def test_get_engine_script_startup_drain_uses_project_name(self):
        from ai_cli.main import get_engine_script

        script = get_engine_script(
            "c",
            "sw-1",
            "c-sw-1",
            "c-sw-",
            "sw",
            project_name="myapp",
        )
        assert 'handoff-drain "$project_name"' in script

    def test_get_engine_script_startup_check_guarded_by_engine(self):
        from ai_cli.main import get_engine_script

        script = get_engine_script(
            "c",
            "sw-1",
            "c-sw-1",
            "c-sw-",
            "sw",
            project_name="myapp",
        )
        assert '"c"' in script


# --- handoff drain ---


class TestHandoffDrain:
    def test_handoff_drain_when_local_file_exists_then_claims_and_writes_prompt_file(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "001-task.md").write_text(
            '---\nid: "1"\ntitle: "local task"\npriority: P1\nproject: myapp\nfor_machine: hetzner\nclaimed_by: null\nclaimed_at: null\n---\n\nDo this.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with (
            patch("sys.argv", ["ai", "internal", "handoff-drain", "myapp", "c-sw-1"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", side_effect=Exception("no nats")),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        prompt_file = state_dir / "cc-resume-prompt-c-sw-1"
        assert prompt_file.exists()
        assert "local task" in prompt_file.read_text()
        assert not (pending / "001-task.md").exists()

    def test_handoff_drain_when_no_local_file_and_nats_unavailable_then_exits_cleanly(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        (handoff_dir / "pending").mkdir(parents=True)
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with (
            patch("sys.argv", ["ai", "internal", "handoff-drain", "myapp", "c-sw-1"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", side_effect=Exception("no nats")),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert not (state_dir / "cc-resume-prompt-c-sw-1").exists()

    def test_handoff_drain_when_too_few_args_then_exits_cleanly(self):
        with patch("sys.argv", ["ai", "internal", "handoff-drain"]):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_handoff_drain_filters_by_project(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "001-wrong.md").write_text(
            '---\nid: "1"\ntitle: "wrong project"\npriority: P0\nproject: other\nclaimed_by: null\nclaimed_at: null\n---\n\nSkip me.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with (
            patch("sys.argv", ["ai", "internal", "handoff-drain", "myapp", "c-sw-1"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", side_effect=Exception("no nats")),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert not (state_dir / "cc-resume-prompt-c-sw-1").exists()
        assert (pending / "001-wrong.md").exists()

    def test_handoff_drain_logs_claimed_event(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "002-task.md").write_text(
            '---\nid: "2"\ntitle: "log task"\npriority: P1\nproject: myapp\nfor_machine: hetzner\nclaimed_by: null\nclaimed_at: null\n---\n\nDo it.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with (
            patch("sys.argv", ["ai", "internal", "handoff-drain", "myapp", "c-sw-2"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", side_effect=Exception("no nats")),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        log = state_dir / "handoff-events.jsonl"
        assert log.exists()
        events = [json.loads(l) for l in log.read_text().strip().split("\n")]
        claimed = [e for e in events if e["event"] == "handoff.claimed"]
        assert any(e["layer"] == "pre_launch_drain" for e in claimed)

    def test_handoff_drain_logs_started_event(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with (
            patch("sys.argv", ["ai", "internal", "handoff-drain", "myapp", "c-sw-3"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=tmp_path / ".handoff-queue"),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=tmp_path / ".handoff-queue"),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", side_effect=Exception("no nats")),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        log = state_dir / "handoff-events.jsonl"
        assert log.exists()
        events = [json.loads(l) for l in log.read_text().strip().split("\n")]
        assert any(e["event"] == "handoff.drain.started" for e in events)

    def test_handoff_drain_logs_local_found_event(self, tmp_path):
        handoff_dir = tmp_path / ".handoff-queue"
        pending = handoff_dir / "pending"
        pending.mkdir(parents=True)
        (pending / "003-task.md").write_text(
            '---\nid: "3"\ntitle: "found task"\npriority: P1\nproject: myapp\nfor_machine: hetzner\nclaimed_by: null\nclaimed_at: null\n---\n\nDo it.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        with (
            patch("sys.argv", ["ai", "internal", "handoff-drain", "myapp", "c-sw-4"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("nats.connect", side_effect=Exception("no nats")),
            patch.dict("os.environ", {"AI_CLI_HOST": "hetzner"}),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        log = state_dir / "handoff-events.jsonl"
        events = [json.loads(l) for l in log.read_text().strip().split("\n")]
        local_found = [e for e in events if e["event"] == "handoff.drain.local_found"]
        assert local_found
        assert local_found[0]["handoff_id"] == 3

    def test_handoff_drain_skips_nats_file_already_in_claimed(self, tmp_path):
        """NATS payload for a handoff already in claimed/ should not be re-claimed."""
        handoff_dir = tmp_path / ".handoff-queue"
        claimed_dir = handoff_dir / "claimed"
        claimed_dir.mkdir(parents=True)
        (claimed_dir / "005-already.md").write_text(
            '---\nid: "5"\ntitle: "already claimed"\npriority: P1\nproject: myapp\nfor_machine: mac\nclaimed_by: prev-session\nclaimed_at: "2026-04-01T00:00:00Z"\n---\n\nDone.\n'
        )
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        nats_payload = {
            "id": 5,
            "title": "already claimed",
            "project": "myapp",
            "priority": "P1",
            "message": "Done.",
            "for_machine": "mac",
            "content": '---\nid: "5"\ntitle: "already claimed"\n---\n\nDone.\n',
            "filename": "005-already.md",
            "ts": 1234567890.0,
        }
        mock_msg = AsyncMock()
        mock_msg.data = json.dumps(nats_payload).encode()
        mock_sub = AsyncMock()
        mock_sub.fetch.side_effect = [
            [mock_msg],
            Exception("timeout"),
        ]
        mock_js = AsyncMock()
        mock_js.pull_subscribe = AsyncMock(return_value=mock_sub)

        async def fake_ensure_stream(_subject):
            pass

        mock_nc = AsyncMock()
        mock_nc.js = mock_js
        mock_nc.close = AsyncMock()
        mock_nc._ensure_stream = fake_ensure_stream

        with (
            patch("sys.argv", ["ai", "internal", "handoff-drain", "myapp", "c-sw-5"]),
            patch("ai_cli.config.load_config", return_value={"messaging": {"nats_servers": ["nats://localhost:4222"]}}),
            patch("ai_cli.config._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.handoff._get_handoff_queue_dir", return_value=handoff_dir),
            patch("ai_cli.config.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.handoff.get_xdg_state_home", return_value=state_dir),
            patch("ai_cli.messaging.NATSClient") as mock_client_cls,
            patch.dict("os.environ", {"AI_CLI_HOST": "mac"}),
        ):
            mock_client_cls.return_value = mock_nc
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert not (state_dir / "cc-resume-prompt-c-sw-5").exists()
        assert not (handoff_dir / "pending" / "005-already.md").exists()


# --- Circus / signal-watch tests ---


class TestSignalWatchCircus:
    def _mock_client(self, status_response=None):
        client = MagicMock()
        if status_response is not None:
            client.send_message.return_value = status_response
        return client

    def test_ensure_circusd_when_already_running_then_no_popen(self, tmp_path):
        # Create a live PID file and endpoint socket so the guard passes
        (tmp_path / "circusd.pid").write_text(str(os.getpid()))
        (tmp_path / "circus.endpoint").touch()
        client = self._mock_client()
        with (
            patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", return_value=client),
            patch("subprocess.Popen") as mock_popen,
        ):
            endpoint = _ensure_circusd()
        assert endpoint == f"ipc://{tmp_path}/circus.endpoint"
        mock_popen.assert_not_called()

    def test_ensure_circusd_when_stale_pid_then_cleans_up_socket_and_starts_new(self, tmp_path):
        # Stale PID (999999999 will never be a live process)
        (tmp_path / "circusd.pid").write_text("999999999")
        (tmp_path / "circus.endpoint").touch()
        (tmp_path / "circus.pubsub").touch()

        with (
            patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", return_value=self._mock_client()),
            patch("subprocess.Popen") as mock_popen,
            patch("shutil.which", return_value="/usr/bin/circusd"),
            patch("time.sleep"),
        ):
            _ensure_circusd()

        # Stale socket files removed and a new circusd was launched
        assert not (tmp_path / "circus.endpoint").exists()
        assert not (tmp_path / "circus.pubsub").exists()
        assert not (tmp_path / "circusd.pid").exists()
        mock_popen.assert_called_once()

    def test_ensure_circusd_when_not_running_then_starts_daemon_and_writes_ini(self, tmp_path):
        call_count = 0

        def client_factory(endpoint, timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionRefusedError("not running")
            return self._mock_client()

        with (
            patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", side_effect=client_factory),
            patch("subprocess.Popen") as mock_popen,
            patch("shutil.which", return_value="/usr/bin/circusd"),
            patch("time.sleep"),
        ):
            endpoint = _ensure_circusd()

        assert endpoint == f"ipc://{tmp_path}/circus.endpoint"
        mock_popen.assert_called_once()
        popen_args = mock_popen.call_args[0][0]
        assert "--daemon" in popen_args
        assert str(tmp_path / "circus.ini") in popen_args
        assert (tmp_path / "circus.ini").exists()
        ini = (tmp_path / "circus.ini").read_text()
        assert "pubsub_endpoint" in ini

    def test_cmd_signal_watch_start_registers_watcher_with_copy_env(self, tmp_path):
        client = self._mock_client()
        with (
            patch("ai_cli.process_manager._ensure_circusd", return_value=f"ipc://{tmp_path}/circus.endpoint"),
            patch("circus.client.CircusClient", return_value=client),
            patch("shutil.which", return_value="/usr/bin/ai"),
        ):
            _cmd_signal_watch_start("myproject", "c-sw-1")

        add_call = next(c for c in client.send_message.call_args_list if c[0][0] == "add")
        options = add_call[1]["options"]
        assert options["copy_env"] is True
        assert add_call[1]["start"] is True

    def test_cmd_signal_watch_start_idempotent_on_second_call(self, tmp_path):
        client = self._mock_client()
        client.send_message.side_effect = [Exception("not found"), None]
        with (
            patch("ai_cli.process_manager._ensure_circusd", return_value=f"ipc://{tmp_path}/circus.endpoint"),
            patch("circus.client.CircusClient", return_value=client),
            patch("shutil.which", return_value="/usr/bin/ai"),
        ):
            _cmd_signal_watch_start("myproject", "c-sw-1")
        calls = [c[0][0] for c in client.send_message.call_args_list]
        assert "rm" in calls
        assert "add" in calls

    def test_cmd_signal_watch_stop_when_circusd_running(self, tmp_path):
        client = self._mock_client()
        with (
            patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", return_value=client),
        ):
            _cmd_signal_watch_stop("c-sw-1")
        client.send_message.assert_called_once_with("rm", name="sw-c-sw-1")

    def test_cmd_signal_watch_stop_when_circusd_not_running_then_silent(self, tmp_path):
        with (
            patch("ai_cli.process_manager.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", side_effect=Exception("zmq error")),
        ):
            _cmd_signal_watch_stop("c-sw-1")

    def test_cmd_signal_watch_status_filters_sw_prefix(self, tmp_path, capsys):
        client = self._mock_client(
            status_response={"statuses": {"sw-c-sw-1": "active", "other-watcher": "active", "sw-c-sw-2": "stopped"}}
        )
        with (
            patch("ai_cli.handoff.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", return_value=client),
        ):
            _cmd_signal_watch_status()
        out = capsys.readouterr().out
        assert "c-sw-1" in out
        assert "c-sw-2" in out
        assert "other-watcher" not in out

    def test_cmd_signal_watch_status_when_circusd_not_running_then_message(self, tmp_path, capsys):
        with (
            patch("ai_cli.handoff.get_xdg_state_home", return_value=tmp_path),
            patch("circus.client.CircusClient", side_effect=Exception("zmq error")),
        ):
            _cmd_signal_watch_status()
        out = capsys.readouterr().out
        assert "not running" in out


# --- ai internal quota-subscriber CLI dispatch ---


class TestQuotaSubscriberCli:
    def test_when_nats_unavailable_then_exits_cleanly(self):
        from nats.errors import NoServersError

        with (
            patch("sys.argv", ["ai", "internal", "quota-subscriber"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("nats.connect", new=AsyncMock(side_effect=NoServersError)),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

    def test_when_message_received_then_records_snapshot_in_db(self, tmp_path):
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="quota")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb
            received_callback["subject"] = subject
            received_callback["durable"] = durable

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = b'{"usage_percent": 55.0, "session_pct": 8.0, "weekly_sonnet_pct": 25.0, "extra_pct": 0.0}'
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        recorded = []

        with (
            patch("sys.argv", ["ai", "internal", "quota-subscriber"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=AsyncMock(side_effect=fake_sleep)),
            patch("ai_cli.quota_db.record_quota_snapshot", side_effect=lambda **kw: recorded.append(kw)),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0

        assert received_callback["subject"] == "quota.snapshot"
        assert received_callback["durable"] == "quota-subscriber-mac"
        assert len(recorded) == 1
        assert recorded[0]["usage_percent"] == 55.0
        assert recorded[0]["session_pct"] == 8.0

    def test_when_record_raises_then_no_exception_propagated(self, tmp_path):
        mock_nc = MagicMock()
        mock_js = MagicMock()
        mock_js.find_stream_name_by_subject = AsyncMock(return_value="quota")
        mock_nc.jetstream.return_value = mock_js
        received_callback = {}

        async def fake_js_subscribe(subject, durable, cb):
            received_callback["cb"] = cb

        mock_js.subscribe = fake_js_subscribe

        async def fake_sleep(_):
            if "cb" in received_callback:
                msg = MagicMock()
                msg.data = b'{"usage_percent": 50.0}'
                msg.ack = AsyncMock()
                await received_callback["cb"](msg)
            raise asyncio.CancelledError

        with (
            patch("sys.argv", ["ai", "internal", "quota-subscriber"]),
            patch("ai_cli.config.load_config", return_value={}),
            patch("nats.connect", new=AsyncMock(return_value=mock_nc)),
            patch("asyncio.sleep", new=AsyncMock(side_effect=fake_sleep)),
            patch("ai_cli.quota_db.record_quota_snapshot", side_effect=RuntimeError("db error")),
        ):
            with pytest.raises(SystemExit) as exc:
                cli()
            assert exc.value.code == 0  # exception swallowed, daemon exits cleanly
