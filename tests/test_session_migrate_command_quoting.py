"""Migration recovery hints use the command-line syntax of the current platform."""

import sys
from pathlib import PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import pytest

from ai_cli import session_migrate as migration
from ai_cli import session_migrate_status as status


@pytest.mark.parametrize(
    ("platform", "repo", "old", "new", "expected"),
    [
        (
            "win32",
            PureWindowsPath(r"D:\projects\myrepo"),
            "myrepo-3",
            "myrepo-box-3",
            r"ai migrate-session myrepo-3 myrepo-box-3 --finish -r D:\projects\myrepo",
        ),
        (
            "win32",
            PureWindowsPath(r"D:\my projects\myrepo"),
            "old name",
            "new name",
            'ai migrate-session "old name" "new name" --finish -r "D:\\my projects\\myrepo"',
        ),
        (
            "linux",
            PurePosixPath("/my projects/repo"),
            "old name",
            "new'name",
            "ai migrate-session 'old name' 'new'\"'\"'name' --finish -r '/my projects/repo'",
        ),
    ],
)
def test_given_platform_and_names_when_finish_hint_is_built_then_arguments_are_quoted(
    monkeypatch, platform, repo, old, new, expected
):
    # Pure paths exercise foreign-platform syntax without changing the host filesystem.
    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", platform)
        command = status.finish_command(old, new, repo)
    assert command == expected


@pytest.mark.parametrize(
    ("platform", "path_type", "root", "expected"),
    [
        ("win32", PureWindowsPath, r"D:\my projects", '"D:\\my projects\\old-agents\\review slot"'),
        ("linux", PurePosixPath, "/my projects", "'/my projects/old-agents/review slot'"),
    ],
)
def test_given_ambiguous_hold_when_accept_hint_is_built_then_full_path_uses_platform_quoting(
    monkeypatch, tmp_path, platform, path_type, root, expected
):
    sources = [path_type(root) / owner / "review slot" for owner in ("old-agents", "other-agents")]
    entries = [
        SimpleNamespace(
            worktree=source,
            dest=source.parent / "destination",
            path_collision=True,
            registered_destination=True,
            held_by=None,
        )
        for source in sources
    ]
    plan = SimpleNamespace(entries=entries, containers=[source.parent for source in sources], markers=[])
    result = migration.MigrationResult("old", "new", tmp_path, tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", platform)
        _, commands = migration._plan_kept_holds(plan, result, path_type(root), tmp_path, ())
    assert commands[str(sources[0])].endswith(f" --accept-kept-hold {expected}")
