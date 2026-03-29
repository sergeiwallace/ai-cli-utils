"""Tests for memory watch daemon."""

from ai_cli.memory import MemoryFileHandler, _find_memory_dirs

from watchdog.events import FileModifiedEvent


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


class TestFindMemoryDirs:
    def test_find_memory_dirs_when_no_cc_dir_then_returns_empty(self, tmp_path):
        from unittest.mock import patch

        with patch("ai_cli.memory.Path") as MockPath:
            MockPath.home.return_value = tmp_path
            result = _find_memory_dirs()
        assert result == []

    def test_find_memory_dirs_when_memory_subdir_exists_then_includes(self, tmp_path):
        cc_projects = tmp_path / ".claude" / "projects"
        project_dir = cc_projects / "test-project"
        memory_dir = project_dir / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "MEMORY.md").write_text("test")

        from unittest.mock import patch

        with patch("ai_cli.memory.Path") as MockPath:
            MockPath.home.return_value = tmp_path
            MockPath.home.return_value = tmp_path

        # Direct test — construct the path the function would check
        from pathlib import Path

        with patch.object(Path, "home", return_value=tmp_path):
            result = _find_memory_dirs()
        assert memory_dir in result
