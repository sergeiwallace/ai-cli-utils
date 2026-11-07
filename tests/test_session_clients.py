"""Each client's transcript probe and rename, against fixture stores in the shapes T-3.0 measured (T-3.11)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from ai_cli import session_clients as sc
from ai_cli.cc_migrate import cc_project_dir

TITLE = "myrepo-box-3"
NEW_TITLE = "myrepo-box-ada"


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "home"


@pytest.fixture
def worktree(tmp_path) -> Path:
    path = tmp_path / "projects" / "myrepo" / ".worktrees" / TITLE
    path.mkdir(parents=True)
    return path.resolve()


def _jsonl(path: Path, *records: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def seed(client: str, home: Path, worktree: Path) -> Path:
    """Write one session record for ``worktree`` in ``client``'s store; return the record's path."""
    if client == "claude-code":
        project = cc_project_dir(worktree, home / ".claude")
        return _jsonl(
            project / "11111111-2222-4333-8444-555555555555.jsonl",
            {"type": "user", "sessionId": "s1", "cwd": str(worktree)},
            {"type": "custom-title", "customTitle": TITLE, "sessionId": "s1"},
        )
    if client == "codex":
        return _jsonl(
            home / ".codex" / "sessions" / "2026" / "10" / "23" / "rollout-2026-10-23T00-00-00-abc.jsonl",
            {"type": "session_meta", "payload": {"id": "c1", "cwd": str(worktree)}},
            {"type": "response_item", "payload": {}},
        )
    if client == "pi":
        return _jsonl(
            home / ".pi" / "agent" / "sessions" / sc.pi_directory(str(worktree)) / "2026-10-23T00-00-00_p1.jsonl",
            {"type": "session", "id": "p1", "cwd": str(worktree)},
            {"type": "session_info", "id": "x", "name": TITLE},
        )
    project_id = "myrepo-box-3"
    (home / ".gemini").mkdir(parents=True, exist_ok=True)
    (home / ".gemini" / "projects.json").write_text(json.dumps({"projects": {str(worktree): project_id}}))
    (home / ".gemini" / "tmp" / project_id).mkdir(parents=True, exist_ok=True)
    (home / ".gemini" / "tmp" / project_id / ".project_root").write_text(f"{worktree}\n")
    return _jsonl(home / ".gemini" / "tmp" / project_id / "chats" / "session-1.jsonl", {"sessionId": "g1"})


def make_root(client: str, home: Path) -> None:
    root = sc.adapter(client, home).store_root()
    root.mkdir(parents=True, exist_ok=True)
    if client == "gemini":
        (home / ".gemini" / "projects.json").write_text(json.dumps({"projects": {}}))


CLIENTS = list(sc.ADAPTERS)


@pytest.mark.parametrize("client", CLIENTS)
def test_given_a_record_for_the_worktree_when_probed_then_it_is_present(client, home, worktree):
    seed(client, home, worktree)

    assert sc.adapter(client, home).probe_transcript(worktree, TITLE).state == sc.PRESENT


@pytest.mark.parametrize("client", CLIENTS)
def test_given_an_accessible_store_with_no_record_when_probed_then_it_is_absent(client, home, worktree):
    make_root(client, home)

    assert sc.adapter(client, home).probe_transcript(worktree, TITLE).state == sc.ABSENT


@pytest.mark.parametrize("client", CLIENTS)
def test_given_no_store_root_when_probed_then_it_is_unknown_never_absent(client, home, worktree):
    probe = sc.adapter(client, home).probe_transcript(worktree, TITLE)

    assert probe.state == sc.UNKNOWN
    assert "missing" in probe.reason


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="permission bits do not deny root or Windows")
@pytest.mark.parametrize("client", CLIENTS)
def test_given_an_unreadable_store_root_when_probed_then_it_is_unknown(client, home, worktree):
    make_root(client, home)
    root = sc.adapter(client, home).store_root()
    root.chmod(0)
    try:
        assert sc.adapter(client, home).probe_transcript(worktree, TITLE).state == sc.UNKNOWN
    finally:
        root.chmod(0o755)


@pytest.mark.parametrize("client", ["codex", "pi"])
def test_given_a_record_that_does_not_parse_when_probed_then_it_is_unknown(client, home, worktree):
    path = seed(client, home, worktree)
    path.write_text("not json\n")

    assert sc.adapter(client, home).probe_transcript(worktree, TITLE).state == sc.UNKNOWN


def test_given_an_unreadable_gemini_registry_when_probed_then_the_mapping_is_invalid_and_unknown(home, worktree):
    seed("gemini", home, worktree)
    (home / ".gemini" / "projects.json").write_text("{not json")

    assert sc.adapter("gemini", home).probe_transcript(worktree, TITLE).state == sc.UNKNOWN


def test_given_a_session_without_a_worktree_when_probed_then_its_title_finds_the_transcript(home, worktree):
    seed("claude-code", home, worktree)

    assert sc.adapter("claude-code", home).probe_transcript(None, TITLE).state == sc.PRESENT
    assert sc.adapter("claude-code", home).probe_transcript(None, NEW_TITLE).state == sc.ABSENT


@pytest.mark.parametrize("client", CLIENTS)
def test_given_a_record_when_renamed_then_only_the_title_and_path_fields_change_and_the_id_stays(
    client, home, worktree, tmp_path
):
    seed(client, home, worktree)
    adapter = sc.adapter(client, home)
    [record] = adapter.records_for(worktree)
    new_worktree = worktree.parent / NEW_TITLE

    result = adapter.rename(record, TITLE, NEW_TITLE, new_worktree)

    after = adapter.records_for(new_worktree) if client != "claude-code" else adapter.records_titled(NEW_TITLE)
    assert after, f"{client} has no record under the new title or path"
    assert after[0].session_id == record.session_id
    assert result.changed


@pytest.mark.parametrize("client", CLIENTS)
def test_given_a_rename_when_rolled_back_then_every_record_is_byte_for_byte_as_before(client, home, worktree):
    path = seed(client, home, worktree)
    adapter = sc.adapter(client, home)
    snapshot = {p: p.read_bytes() for p in adapter.store_root().parent.rglob("*") if p.is_file()}
    [record] = adapter.records_for(worktree)

    changes = adapter.rename(record, TITLE, NEW_TITLE, worktree.parent / NEW_TITLE).changed
    failed = sc.rollback(changes)

    assert failed == []
    after = {p: p.read_bytes() for p in adapter.store_root().parent.rglob("*") if p.is_file()}
    assert after == snapshot
    assert path.exists()


# --- what a lookup reads -----------------------------------------------------------------


@pytest.fixture
def reads(monkeypatch) -> list[tuple[str, Path]]:
    """Every file read through ``Path.read_bytes`` or opened through ``Path.open``, with which one."""
    opened: list[tuple[str, Path]] = []
    real_read_bytes, real_open = Path.read_bytes, Path.open

    def read_bytes(self):
        opened.append(("read_bytes", Path(self)))
        return real_read_bytes(self)

    def open_(self, *args, **kwargs):
        opened.append(("open", Path(self)))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "open", open_)
    return opened


def _other_worktrees(worktree: Path, count: int = 30) -> list[Path]:
    return [worktree.parent / f"other-{index}" for index in range(count)]


def test_given_a_store_of_many_projects_when_a_claude_code_worktree_is_looked_up_then_only_its_own_directory_is_read(
    home, worktree, reads
):
    for other in _other_worktrees(worktree):
        seed("claude-code", home, other)
    own = seed("claude-code", home, worktree)
    adapter = sc.adapter("claude-code", home)
    reads.clear()

    records = adapter.records_for(worktree)
    probe = adapter.probe_transcript(worktree, TITLE)

    assert [record.path for record in records] == [own]
    assert records[0].title == TITLE
    assert probe.state == sc.PRESENT
    assert {path.parent for _, path in reads} == {own.parent}


def test_given_a_store_of_many_projects_when_an_absent_claude_code_worktree_is_probed_then_it_is_absent_reading_nothing(
    home, worktree, reads
):
    for other in _other_worktrees(worktree):
        seed("claude-code", home, other)
    reads.clear()

    probe = sc.adapter("claude-code", home).probe_transcript(worktree, TITLE)

    assert probe.state == sc.ABSENT
    assert reads == []


def test_given_an_empty_claude_code_project_directory_when_probed_then_it_is_present(home, worktree):
    cc_project_dir(worktree, home / ".claude").mkdir(parents=True)

    assert sc.adapter("claude-code", home).probe_transcript(worktree, TITLE).state == sc.PRESENT


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="permission bits do not deny root or Windows")
def test_given_an_unreadable_claude_code_transcript_when_probed_then_it_is_unknown(home, worktree):
    path = seed("claude-code", home, worktree)
    path.chmod(0)
    try:
        assert sc.adapter("claude-code", home).probe_transcript(worktree, TITLE).state == sc.UNKNOWN
    finally:
        path.chmod(0o644)


def test_given_a_title_written_after_the_working_directory_when_listed_then_the_last_custom_title_is_read(
    home, worktree
):
    project = cc_project_dir(worktree, home / ".claude")
    _jsonl(
        project / "s.jsonl",
        {"type": "user", "cwd": str(worktree)},
        {"type": "assistant", "message": {"content": "x" * 64}},
        {"type": "custom-title", "customTitle": "first-title"},
        {"type": "custom-title", "customTitle": TITLE},
    )

    [record] = sc.adapter("claude-code", home).all_records()

    assert (record.cwd, record.title) == (str(worktree), TITLE)


def test_given_many_rollouts_when_a_codex_worktree_is_probed_then_reading_stops_at_its_rollout(home, worktree, reads):
    own = seed("codex", home, worktree)
    for index, other in enumerate(_other_worktrees(worktree)):
        _jsonl(
            own.parent / f"rollout-2026-10-23T01-00-{index:02d}-x.jsonl",
            {"type": "session_meta", "payload": {"id": f"o{index}", "cwd": str(other)}},
        )
    reads.clear()

    probe = sc.adapter("codex", home).probe_transcript(worktree, TITLE)

    assert probe.state == sc.PRESENT
    assert {path for _, path in reads} == {own}


def test_given_an_unreadable_rollout_and_no_match_when_a_codex_worktree_is_probed_then_it_is_unknown(home, worktree):
    path = seed("codex", home, worktree)
    path.with_name("rollout-2026-10-23T09-00-00-bad.jsonl").write_text("not json\n")
    path.unlink()

    assert sc.adapter("codex", home).probe_transcript(worktree, TITLE).state == sc.UNKNOWN


def test_given_many_pi_sessions_when_a_worktree_is_looked_up_then_only_its_session_is_read_past_the_header(
    home, worktree, reads
):
    for other in _other_worktrees(worktree):
        seed("pi", home, other)
    own = seed("pi", home, worktree)
    reads.clear()

    records = sc.adapter("pi", home).records_for(worktree)

    assert [(record.path, record.title) for record in records] == [(own, TITLE)]
    assert {path for kind, path in reads if kind == "read_bytes"} == {own}


def test_given_an_unreadable_pi_session_in_another_directory_when_probed_then_it_does_not_hold_the_worktree(
    home, worktree
):
    make_root("pi", home)
    seed("pi", home, _other_worktrees(worktree, 1)[0]).write_text("not json\n")

    assert sc.adapter("pi", home).probe_transcript(worktree, TITLE).state == sc.ABSENT


def test_given_many_gemini_projects_when_a_worktree_is_looked_up_then_only_its_project_chats_are_read(
    home, worktree, reads
):
    own = seed("gemini", home, worktree)
    projects = {str(worktree): own.parent.parent.name}
    for index, other in enumerate(_other_worktrees(worktree)):
        _jsonl(home / ".gemini" / "tmp" / f"p{index}" / "chats" / "session-1.jsonl", {"sessionId": f"g{index}"})
        projects[str(other)] = f"p{index}"
    (home / ".gemini" / "projects.json").write_text(json.dumps({"projects": projects}))
    reads.clear()

    records = sc.adapter("gemini", home).records_for(worktree)

    assert [record.path for record in records] == [own]
    assert {path for _, path in reads if path.suffix == ".jsonl"} == {own}
