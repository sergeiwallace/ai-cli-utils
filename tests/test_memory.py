"""Tests for memory watch daemon."""

from unittest.mock import MagicMock, patch

from watchdog.events import FileCreatedEvent, FileModifiedEvent

from ai_cli.memory import MemoryFileHandler, _find_memory_dirs


class TestMemoryFileHandler:
    def test_on_modified_when_memory_md_changed_then_starts_dream(self):
        started = []
        settled = []
        handler = MemoryFileHandler(
            on_write_start=lambda p: started.append(p),
            on_write_settle=lambda: settled.append(True),
        )
        event = FileModifiedEvent("/home/user/.claude/projects/abc/memory/MEMORY.md")
        handler.on_modified(event)
        assert len(started) == 1
        assert handler.dreaming is True

    def test_on_modified_when_non_memory_file_then_ignores(self):
        started = []
        settled = []
        handler = MemoryFileHandler(
            on_write_start=lambda p: started.append(p),
            on_write_settle=lambda: settled.append(True),
        )
        event = FileModifiedEvent("/home/user/.claude/projects/abc/config.json")
        handler.on_modified(event)
        assert len(started) == 0
        assert handler.dreaming is False

    def test_on_modified_when_already_dreaming_then_no_duplicate_start(self):
        started = []
        handler = MemoryFileHandler(
            on_write_start=lambda p: started.append(p),
            on_write_settle=lambda: None,
        )
        event = FileModifiedEvent("/home/user/.claude/projects/abc/memory/MEMORY.md")
        handler.on_modified(event)
        handler.on_modified(event)
        handler.on_modified(event)
        assert len(started) == 1

    def test_check_settle_when_debounce_elapsed_then_emits_settle(self):
        settled = []
        handler = MemoryFileHandler(
            on_write_start=lambda p: None,
            on_write_settle=lambda: settled.append(True),
        )
        handler._debounce_s = 0.0  # Instant settle for test
        event = FileModifiedEvent("/home/user/.claude/projects/abc/memory/MEMORY.md")
        handler.on_modified(event)
        assert handler.dreaming is True
        handler.check_settle()
        assert handler.dreaming is False
        assert len(settled) == 1

    def test_check_settle_when_still_writing_then_stays_dreaming(self):
        settled = []
        handler = MemoryFileHandler(
            on_write_start=lambda p: None,
            on_write_settle=lambda: settled.append(True),
        )
        handler._debounce_s = 999.0  # Never settles
        event = FileModifiedEvent("/home/user/.claude/projects/abc/memory/MEMORY.md")
        handler.on_modified(event)
        handler.check_settle()
        assert handler.dreaming is True
        assert len(settled) == 0

    def test_check_settle_when_not_dreaming_then_no_op(self):
        settled = []
        handler = MemoryFileHandler(
            on_write_start=lambda p: None,
            on_write_settle=lambda: settled.append(True),
        )
        handler.check_settle()
        assert len(settled) == 0

    def test_on_modified_when_non_file_event_then_returns_early(self):
        """Line 36: non-FileModifiedEvent triggers early return."""
        started = []
        handler = MemoryFileHandler(
            on_write_start=lambda p: started.append(p),
            on_write_settle=lambda: None,
        )
        event = FileCreatedEvent("/home/user/.claude/projects/abc/memory/MEMORY.md")
        handler.on_modified(event)
        assert len(started) == 0
        assert handler.dreaming is False


class TestFindMemoryDirs:
    def test_find_memory_dirs_when_no_cc_dir_then_returns_empty(self, tmp_path):
        from pathlib import Path
        from unittest.mock import patch

        with patch.object(Path, "home", return_value=tmp_path):
            result = _find_memory_dirs()
        assert result == []

    def test_find_memory_dirs_when_memory_subdir_exists_then_includes(self, tmp_path):
        cc_projects = tmp_path / ".claude" / "projects"
        project_dir = cc_projects / "test-project"
        memory_dir = project_dir / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "MEMORY.md").write_text("test")

        from pathlib import Path
        from unittest.mock import patch

        with patch.object(Path, "home", return_value=tmp_path):
            result = _find_memory_dirs()
        assert memory_dir in result

    def test_find_memory_dirs_when_file_in_projects_dir_then_skips(self, tmp_path):
        """Line 60: continue when item is a file, not a directory."""
        from pathlib import Path

        cc_projects = tmp_path / ".claude" / "projects"
        cc_projects.mkdir(parents=True)
        # Create a file (not a directory) inside projects dir
        (cc_projects / "some-file.txt").write_text("not a dir")

        with patch.object(Path, "home", return_value=tmp_path):
            result = _find_memory_dirs()
        assert result == []

    def test_find_memory_dirs_when_no_memory_subdir_but_has_memory_md_then_watches_project_dir(self, tmp_path):
        """Lines 64-65: project dir has MEMORY.md but no memory/ subdir."""
        from pathlib import Path

        cc_projects = tmp_path / ".claude" / "projects"
        project_dir = cc_projects / "test-project"
        project_dir.mkdir(parents=True)
        (project_dir / "MEMORY.md").write_text("# Memory")

        with patch.object(Path, "home", return_value=tmp_path):
            result = _find_memory_dirs()
        assert project_dir in result


class TestMemoryWatch:
    def test_memory_watch_when_already_running_then_returns_2(self):
        with patch("ai_cli.sync._acquire_pid_file", return_value=False):
            from ai_cli.memory import memory_watch

            result = memory_watch()
        assert result == 2

    def test_memory_watch_when_nats_unavailable_then_returns_1(self):
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            pass  # nc stays None

        mock_client.connect = fake_connect

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file"),
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
        ):
            from ai_cli.memory import memory_watch

            result = memory_watch()
        assert result == 1

    def test_memory_watch_when_no_memory_dirs_then_returns_1(self):
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            mock_client.nc = MagicMock()

        async def fake_close():
            pass

        mock_client.connect = fake_connect
        mock_client.close = MagicMock(side_effect=fake_close)

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file") as mock_release,
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.memory._find_memory_dirs", return_value=[]),
        ):
            from ai_cli.memory import memory_watch

            result = memory_watch()
        assert result == 1
        mock_release.assert_called_with("memory-watch")

    def test_memory_watch_when_interrupt_then_returns_0(self, tmp_path):
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            mock_client.nc = MagicMock()

        async def fake_close():
            pass

        mock_client.connect = fake_connect
        mock_client.close = MagicMock(side_effect=fake_close)

        mock_observer = MagicMock()

        def fake_sleep(_interval):
            raise KeyboardInterrupt

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file") as mock_release,
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.memory._find_memory_dirs", return_value=[tmp_path]),
            patch("ai_cli.memory.Observer", return_value=mock_observer),
            patch("time.sleep", fake_sleep),
        ):
            from ai_cli.memory import memory_watch

            result = memory_watch()
        assert result == 0
        mock_observer.start.assert_called_once()
        mock_observer.stop.assert_called_once()
        mock_observer.join.assert_called_once()
        mock_release.assert_called_with("memory-watch")

    def test_memory_watch_when_callbacks_invoked_then_publishes(self, tmp_path):
        """Covers lines 99-103, 106-110: on_write_start and on_write_settle callback bodies."""
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            mock_client.nc = MagicMock()

        async def fake_publish(*args, **kwargs):
            pass

        async def fake_close():
            pass

        mock_client.connect = fake_connect
        mock_client.publish = MagicMock(side_effect=fake_publish)
        mock_client.close = MagicMock(side_effect=fake_close)

        mock_observer = MagicMock()
        captured_callbacks = []

        original_init = MemoryFileHandler.__init__

        def capturing_init(self, on_write_start, on_write_settle):
            captured_callbacks.append((on_write_start, on_write_settle))
            original_init(self, on_write_start, on_write_settle)

        call_count = 0

        def fake_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count == 1 and captured_callbacks:
                on_start, on_settle = captured_callbacks[0]
                on_start("/path/to/MEMORY.md")
                on_settle()
            raise KeyboardInterrupt

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file"),
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.memory._find_memory_dirs", return_value=[tmp_path]),
            patch("ai_cli.memory.Observer", return_value=mock_observer),
            patch("ai_cli.memory.MemoryFileHandler.__init__", capturing_init),
            patch("time.sleep", fake_sleep),
        ):
            from ai_cli.memory import memory_watch

            memory_watch()

        assert mock_client.publish.call_count >= 2
        subjects = [call.args[0] for call in mock_client.publish.call_args_list]
        assert "memory.dream.started" in subjects
        assert "memory.dream.completed" in subjects

    def test_memory_watch_when_publish_raises_then_logs_error(self, tmp_path, capsys):
        """Covers lines 102-103, 109-110: except Exception in callback publish calls."""
        mock_client = MagicMock()
        mock_client.nc = None

        async def fake_connect():
            mock_client.nc = MagicMock()

        async def fake_publish_raises(*args, **kwargs):
            raise Exception("publish error")

        async def fake_close():
            pass

        mock_client.connect = fake_connect
        mock_client.publish = MagicMock(side_effect=fake_publish_raises)
        mock_client.close = MagicMock(side_effect=fake_close)

        mock_observer = MagicMock()
        captured_callbacks = []

        original_init = MemoryFileHandler.__init__

        def capturing_init(self, on_write_start, on_write_settle):
            captured_callbacks.append((on_write_start, on_write_settle))
            original_init(self, on_write_start, on_write_settle)

        call_count = 0

        def fake_sleep(_):
            nonlocal call_count
            call_count += 1
            if call_count == 1 and captured_callbacks:
                on_start, on_settle = captured_callbacks[0]
                on_start("/path/to/MEMORY.md")
                on_settle()
            raise KeyboardInterrupt

        with (
            patch("ai_cli.sync._acquire_pid_file", return_value=True),
            patch("ai_cli.sync._release_pid_file"),
            patch("ai_cli.messaging.NATSClient", return_value=mock_client),
            patch("ai_cli.memory._find_memory_dirs", return_value=[tmp_path]),
            patch("ai_cli.memory.Observer", return_value=mock_observer),
            patch("ai_cli.memory.MemoryFileHandler.__init__", capturing_init),
            patch("time.sleep", fake_sleep),
        ):
            from ai_cli.memory import memory_watch

            memory_watch()

        err = capsys.readouterr().err
        assert "failed to publish" in err
