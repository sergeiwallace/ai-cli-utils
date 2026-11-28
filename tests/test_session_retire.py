"""``ai retire-session NAME`` retires a dead session's home slot, losing no commit.

Every test drives the real command against a temp repo with an ``origin`` remote and
a registered slot ``.worktrees/myrepo-3`` whose branch ``wt-myrepo-3`` is contained in
``origin/main``. The live arms (a Claude Code process, a tmux session, a fresh
heartbeat) and the content arms (uncommitted, untracked, unpushed) each refuse with
nothing written; the dead arm anchors, removes and records.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest
from conftest import run_cli, snapshot_tree

from ai_cli import session_registry
from ai_cli.canonical_worktrees import get_canonical_worktree_registry_path, register_canonical_worktree
from ai_cli.cc_migrate import cc_project_dir
from ai_cli.config import get_xdg_state_home
from ai_cli.stale_session_reaper import write_heartbeat

NAME = "myrepo-3"
BRANCH = f"wt-{NAME}"
ANCHORS = "refs/preserved/retire-session"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _ref(repo: Path, ref: str) -> str | None:
    res = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref], cwd=repo, capture_output=True, text=True, check=False
    )
    return res.stdout.strip() if res.returncode == 0 else None


@pytest.fixture
def slot(tmp_path):
    """A registered, clean, fully pushed slot of a dead session, with a transcript directory."""
    claude = Path.home() / ".claude"
    state = get_xdg_state_home()
    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)

    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    # A commit can leave auto-maintenance running detached, writing under .git while a
    # nothing-changed snapshot is compared.
    _git("config", "maintenance.auto", "false", cwd=repo)
    _git("config", "gc.auto", "0", cwd=repo)
    (repo / ".gitignore").write_text("*.log\n")
    (repo / "README.md").write_text("hi\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    _git("remote", "add", "origin", str(origin), cwd=repo)
    _git("push", "-q", "origin", "main", cwd=repo)
    _git("fetch", "-q", "origin", cwd=repo)
    repo = repo.resolve()
    wt = repo / ".worktrees" / NAME
    _git("worktree", "add", "-q", "--no-track", str(wt), "-b", BRANCH, "origin/main", cwd=repo)
    register_canonical_worktree(wt, engine="c", session_name=f"c-{NAME}")

    project = cc_project_dir(wt, claude)
    project.mkdir(parents=True)
    (project / "3a3a3a3a-0000-4000-8000-000000000003.jsonl").write_text('{"type": "user"}\n')
    return {"repo": repo, "wt": wt, "claude": claude, "state": state, "project": project}


def _retire(repo: Path, *flags: str, tmux: list[str] | None = None):
    with patch("ai_cli.session._tmux_session_names", return_value=list(tmux or [])):
        return run_cli(["ai", "retire-session", NAME, "-r", str(repo), *flags])


def _registry_paths() -> list[str]:
    return [e["path"] for e in json.loads(get_canonical_worktree_registry_path().read_text())["worktrees"]]


def _everything(slot) -> dict:
    """Every tree and ref a retirement may touch, for a nothing-changed check."""
    trees: dict = {name: snapshot_tree(slot[name]) for name in ("repo", "claude", "state")}
    trees["refs"] = _git("for-each-ref", cwd=slot["repo"])
    trees["worktrees"] = _git("worktree", "list", "--porcelain", cwd=slot["repo"])
    trees["registry"] = get_canonical_worktree_registry_path().read_text(encoding="utf-8")
    return trees


def _refused(slot, *flags: str, tmux: list[str] | None = None) -> str:
    """Run the retirement, assert it refused with nothing written, and return stderr."""
    before = _everything(slot)
    code, out, err = _retire(slot["repo"], *flags, tmux=tmux)
    assert code == 1, out + err
    assert _everything(slot) == before
    assert not (slot["state"] / "session-retirements.jsonl").exists()
    return err


def test_given_a_dead_clean_slot_when_retired_then_it_is_anchored_removed_unregistered_and_recorded(slot):
    repo, wt = slot["repo"], slot["wt"]
    (wt / "debug.log").write_text("ignored scratch\n")
    tip = _ref(repo, f"refs/heads/{BRANCH}")
    assert str(wt) in _registry_paths()

    code, out, err = _retire(repo, "-y")
    assert code == 0, out + err

    assert not wt.exists()
    assert f"worktree {wt.as_posix()}\n" not in _git("worktree", "list", "--porcelain", cwd=repo)
    assert str(wt) not in _registry_paths()
    assert _ref(repo, f"{ANCHORS}/{BRANCH}") == tip
    assert _ref(repo, f"refs/heads/{BRANCH}") == tip
    assert slot["project"].is_dir()
    assert f"Claude Code project dir left in place: {slot['project']}" in out

    rows = (slot["state"] / "session-retirements.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert (row["name"], row["repo"], row["worktree"], row["branch"]) == (NAME, str(repo), str(wt), BRANCH)
    assert row["anchors"] == {f"{ANCHORS}/{BRANCH}": tip}
    assert row["branch_tips"] == {BRANCH: tip}
    assert row["time"]


def test_given_a_dry_run_when_retiring_then_the_plan_is_printed_and_nothing_changes(slot):
    tip = _ref(slot["repo"], f"refs/heads/{BRANCH}")
    before = _everything(slot)
    code, out, err = _retire(slot["repo"], "-n")
    assert code == 0, out + err
    assert _everything(slot) == before
    assert not (slot["state"] / "session-retirements.jsonl").exists()
    for expected in (
        f"{NAME}: would retire {slot['wt']}",
        f"anchor: {ANCHORS}/{BRANCH} -> {tip} (write); branch {BRANCH} stays",
        "canonical registry: drop 1 entry(ies)",
        f"ledger: append to {slot['state'] / 'session-retirements.jsonl'}",
        f"Claude Code project dir left in place: {slot['project']}",
    ):
        assert expected in out, f"{expected!r} missing from:\n{out}"
    assert all("[retire]" in line for line in (out + err).splitlines() if line.strip())


def test_given_a_running_claude_code_process_in_the_slot_when_retiring_then_it_is_refused(slot):
    sessions = slot["claude"] / "sessions"
    sessions.mkdir()
    (sessions / f"{os.getpid()}.json").write_text(
        json.dumps({"pid": os.getpid(), "name": "other-1", "cwd": str(slot["wt"] / "src")})
    )
    err = _refused(slot, "-y")
    assert "still running in the slot being retired" in err


def test_given_its_tmux_session_still_exists_when_retiring_then_it_is_refused(slot):
    err = _refused(slot, "-n", tmux=[f"c-r-{NAME}"])
    assert f"tmux session c-r-{NAME} still hosts {NAME}" in err


def test_given_a_fresh_heartbeat_when_retiring_then_it_is_refused(slot):
    assert write_heartbeat(slot["state"], f"c-{NAME}", "gen1")
    err = _refused(slot, "-y")
    assert f"{NAME} may still be running:" in err and f"(c-{NAME}) beat" in err


def test_given_only_a_stale_heartbeat_when_retiring_then_it_is_retired(slot):
    assert write_heartbeat(slot["state"], f"c-{NAME}", "gen1", monotonic_clock=lambda: time.monotonic() - 3600)
    assert write_heartbeat(slot["state"], f"c-{NAME}", "gen2", boot_generation="psutil:0.000000")
    assert write_heartbeat(slot["state"], "c-other-1", "gen3")
    code, out, err = _retire(slot["repo"], "-y")
    assert code == 0, out + err
    assert not slot["wt"].exists()


def test_given_an_unreadable_heartbeat_of_the_session_when_retiring_then_it_is_refused(slot):
    assert write_heartbeat(slot["state"], f"c-{NAME}", "gen1", monotonic_clock=lambda: time.monotonic() - 3600)
    (record,) = (slot["state"] / "session-heartbeats").glob("*.json")
    record.write_text("{not json")
    err = _refused(slot, "-y")
    assert f"{record} is unreadable" in err


def test_given_uncommitted_and_untracked_work_when_retiring_then_each_file_is_named_and_it_is_refused(slot):
    (slot["wt"] / "README.md").write_text("edited\n")
    (slot["wt"] / "notes.txt").write_text("draft\n")
    err = _refused(slot, "-y")
    assert "uncommitted: README.md" in err
    assert "untracked: notes.txt" in err


def test_given_a_commit_in_neither_upstream_nor_origin_main_when_retiring_then_it_is_refused(slot):
    _git("commit", "-q", "--allow-empty", "-m", "local only", cwd=slot["wt"])
    err = _refused(slot, "-n")
    assert f"{BRANCH} has 1 commit(s) not in refs/remotes/origin/main" in err


def test_given_a_topic_branch_pushed_to_its_upstream_when_retired_then_both_tips_are_anchored(slot):
    repo, wt = slot["repo"], slot["wt"]
    _git("switch", "-q", "-c", "topic", cwd=wt)
    _git("commit", "-q", "--allow-empty", "-m", "topic work", cwd=wt)
    _git("push", "-q", "-u", "origin", "topic", cwd=wt)
    topic, parked = _ref(repo, "refs/heads/topic"), _ref(repo, f"refs/heads/{BRANCH}")

    code, out, err = _retire(repo, "-y")
    assert code == 0, out + err
    assert not wt.exists()
    assert _ref(repo, f"{ANCHORS}/topic") == topic == _ref(repo, "refs/heads/topic")
    assert _ref(repo, f"{ANCHORS}/{BRANCH}") == parked == _ref(repo, f"refs/heads/{BRANCH}")


def test_given_an_unregistered_slot_when_retiring_then_it_is_refused(slot):
    code, _out, err = run_cli(["ai", "retire-session", "myrepo-9", "-r", str(slot["repo"]), "-y"])
    assert code == 1
    assert "is not a registered worktree" in err


def test_given_the_help_when_printed_then_it_names_every_option_and_the_rename_verb():
    code, out, _err = run_cli(["ai", "retire-session", "--help"])
    assert code == 0
    for flag in ("-n, --dry-run", "-y, --yes", "-r, --repo", "migrate-session"):
        assert flag in out


def _commit(wt: Path, files: dict[str, str], message: str) -> None:
    for rel, text in files.items():
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / rel).write_text(text)
    _git("add", "-A", cwd=wt)
    _git("commit", "-qm", message, cwd=wt)


def _land_on_main(repo: Path, files: dict[str, str], message: str) -> None:
    """Commit ``files`` on the main checkout, push it and refresh ``origin/main``."""
    _commit(repo, files, message)
    _git("push", "-q", "origin", "main", cwd=repo)
    _git("fetch", "-q", "origin", cwd=repo)


@pytest.fixture
def squashed(slot):
    """The slot's branch made two commits; main then landed their content as one squash."""
    wt = slot["wt"]
    _commit(wt, {"README.md": "hi\nsecond line\n"}, "edit readme")
    _commit(wt, {"src/app.py": "def run():\n    return 1\n"}, "add app")
    _land_on_main(
        slot["repo"], {"README.md": "hi\nsecond line\n", "src/app.py": "def run():\n    return 1\n"}, "squash"
    )
    return slot


def test_given_a_squash_merged_branch_when_retired_then_it_is_removed_and_its_original_tip_anchored(squashed):
    repo, wt = squashed["repo"], squashed["wt"]
    tip = _ref(repo, f"refs/heads/{BRANCH}")
    assert _git("rev-list", "--count", f"{tip}", "--not", "origin/main", cwd=repo).strip() == "2"

    code, out, err = _retire(repo, "-y")
    assert code == 0, out + err
    assert not wt.exists()
    assert _ref(repo, f"{ANCHORS}/{BRANCH}") == tip == _ref(repo, f"refs/heads/{BRANCH}")


def test_given_a_squash_merged_branch_whose_line_main_rewrote_later_when_retired_then_it_is_removed(squashed):
    _land_on_main(squashed["repo"], {"src/app.py": "def run():\n    return 2\n"}, "later rewrite")
    code, out, err = _retire(squashed["repo"], "-y")
    assert code == 0, out + err
    assert not squashed["wt"].exists()


def test_given_a_squash_merged_branch_with_one_line_not_on_main_when_retiring_then_it_names_the_file(squashed):
    _commit(squashed["wt"], {"src/extra.py": "UNSHIPPED = True\n"}, "local only")
    err = _refused(squashed, "-y")
    assert f"{BRANCH} has 3 commit(s) not in refs/remotes/origin/main" in err
    assert "src/extra.py" in err and "UNSHIPPED = True" in err


def test_given_a_line_main_never_carried_inserted_beside_a_rewritten_one_when_retiring_then_it_is_refused(squashed):
    _commit(squashed["wt"], {"src/app.py": "def run():\n    NEW = 1\n    return 3\n"}, "insert and rewrite")
    _land_on_main(squashed["repo"], {"src/app.py": "def run():\n    return 2\n"}, "later rewrite")
    err = _refused(squashed, "-y")
    assert "src/app.py" in err and "NEW = 1" in err


def test_given_the_branch_tracks_origin_main_when_refused_then_the_message_names_that_ref_once(slot):
    _git("branch", "-q", "--set-upstream-to", "origin/main", BRANCH, cwd=slot["wt"])
    _commit(slot["wt"], {"notes.md": "draft\n"}, "local only")
    err = _refused(slot, "-n")
    assert f"{BRANCH} has 1 commit(s) not in refs/remotes/origin/main" in err
    assert " nor " not in err


def test_given_a_topic_branch_with_a_separate_upstream_when_refused_then_the_message_names_both_refs(slot):
    wt = slot["wt"]
    _git("switch", "-q", "-c", "topic", cwd=wt)
    _commit(wt, {"pushed.md": "pushed\n"}, "pushed")
    _git("push", "-q", "-u", "origin", "topic", cwd=wt)
    _commit(wt, {"local.md": "local\n"}, "local only")
    err = _refused(slot, "-n")
    assert "topic has 1 commit(s) in neither refs/remotes/origin/topic nor refs/remotes/origin/main" in err


def test_given_a_base_line_both_sides_rewrote_and_main_won_when_retired_then_it_is_removed(slot):
    _commit(slot["wt"], {"README.md": "hello\n"}, "reword")
    _land_on_main(slot["repo"], {"README.md": "hey\n"}, "reword differently")
    code, out, err = _retire(slot["repo"], "-y")
    assert code == 0, out + err
    assert not slot["wt"].exists()


def test_given_a_binary_file_main_never_carried_when_retiring_then_it_is_refused(squashed):
    (squashed["wt"] / "logo.bin").write_bytes(b"\x00\x01\x02binary")
    _git("add", "-A", cwd=squashed["wt"])
    _git("commit", "-qm", "add logo", cwd=squashed["wt"])
    err = _refused(squashed, "-y")
    assert "logo.bin adds `(binary content)`" in err


def test_given_a_retired_name_reissued_with_a_new_tip_when_retired_again_then_both_tips_are_anchored(slot):
    repo, wt = slot["repo"], slot["wt"]
    first_tip = _ref(repo, f"refs/heads/{BRANCH}")
    code, out, err = _retire(repo, "-y")
    assert code == 0, out + err

    # The name is reissued: main moves on, the same branch name gets a new tip and a new slot.
    _land_on_main(repo, {"next.txt": "next\n"}, "next")
    _git("fetch", "-q", "origin", cwd=repo)
    _git("branch", "-f", BRANCH, "origin/main", cwd=repo)
    _git("worktree", "add", "-q", str(wt), BRANCH, cwd=repo)
    register_canonical_worktree(wt, engine="c", session_name=f"c-{NAME}")
    second_tip = _ref(repo, f"refs/heads/{BRANCH}")
    assert second_tip != first_tip

    code, out, err = _retire(repo, "-y")
    assert code == 0, out + err

    assert not wt.exists()
    assert _ref(repo, f"{ANCHORS}/{BRANCH}") == first_tip
    second_anchor = f"{ANCHORS}/{BRANCH}--{second_tip[:12]}"
    assert _ref(repo, second_anchor) == second_tip
    rows = (slot["state"] / "session-retirements.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(r)["anchors"] for r in rows] == [
        {f"{ANCHORS}/{BRANCH}": first_tip},
        {second_anchor: second_tip},
    ]


def _session_rows(*names: str) -> None:
    path = session_registry.registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"id": f"id-{n}", "kind": "local", "name": n, "run_state": "exited", "boot_recipe": {"session_name": n}}
        for n in names
    ]
    path.write_text(json.dumps({"schema": 2, "machine": "box", "sessions": rows}), encoding="utf-8")


def _session_row_names() -> list[str]:
    return [r["name"] for r in session_registry.load_registry()["sessions"]]


def test_given_session_registry_rows_for_the_name_when_retired_then_only_those_rows_are_dropped(slot):
    _session_rows(f"c-{NAME}", f"c-r-{NAME}", "c-myrepo-4")
    code, out, err = _retire(slot["repo"], "-y")
    assert code == 0, out + err
    assert _session_row_names() == ["c-myrepo-4"]
    assert "session registry: drop 2 row(s)" in out


def test_given_session_registry_rows_for_the_name_when_dry_run_then_rows_stay_and_the_plan_names_them(slot):
    _session_rows(f"c-{NAME}", "c-myrepo-4")
    code, out, err = _retire(slot["repo"], "-n")
    assert code == 0, out + err
    assert _session_row_names() == [f"c-{NAME}", "c-myrepo-4"]
    assert "session registry: drop 1 row(s)" in out


def test_given_its_tmux_session_still_exists_when_retiring_then_its_session_row_stays(slot):
    _session_rows(f"c-{NAME}")
    _refused(slot, "-y", tmux=[f"c-{NAME}"])
    assert _session_row_names() == [f"c-{NAME}"]


def _session_row_at(name: str, cwd: Path) -> None:
    path = session_registry.registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "id": f"id-{name}",
        "kind": "local",
        "name": name,
        "run_state": "exited",
        "cwd": str(cwd),
        "boot_recipe": {"session_name": name, "cwd": str(cwd)},
    }
    path.write_text(json.dumps({"schema": 2, "machine": "box", "sessions": [row]}), encoding="utf-8")


def test_given_a_record_naming_the_repository_root_when_retiring_a_name_with_no_slot_then_it_is_refused(slot):
    _session_row_at("c-myrepo-9", slot["repo"])
    before = _everything(slot)
    code, out, err = run_cli(["ai", "retire-session", "myrepo-9", "-r", str(slot["repo"]), "-y"])
    assert code == 1, out + err
    assert "is not a registered worktree" in err
    assert _everything(slot) == before


def test_given_a_record_naming_another_worktree_than_the_slot_when_retiring_then_it_is_refused_naming_both(slot):
    other = slot["repo"].parent / "elsewhere"
    _git("worktree", "add", "-q", "--no-track", str(other), "-b", "probe-elsewhere", "origin/main", cwd=slot["repo"])
    _session_row_at(f"c-{NAME}", other.resolve())
    err = _refused(slot, "-y")
    assert str(slot["wt"]) in err and str(other.resolve()) in err
    assert other.is_dir()


def _admin_dir(wt: Path) -> Path:
    return Path(_git("rev-parse", "--absolute-git-dir", cwd=wt).strip()).resolve()


@pytest.fixture
def interrupted(slot, monkeypatch):
    """The slot as a launch killed mid ``git worktree add`` leaves it, quiet past the threshold.

    Git locks the new checkout as ``initializing``, takes ``index.lock`` and writes the
    files before the index, so a kill leaves no index and each file empty or cut short.
    """
    admin = _admin_dir(slot["wt"])
    (admin / "index").unlink()
    (admin / "index.lock").write_bytes(b"")
    (admin / "locked").write_text("initializing\n")
    (slot["wt"] / ".gitignore").write_bytes(b"")
    (slot["wt"] / "README.md").write_bytes(b"h")
    monkeypatch.setattr("ai_cli.session_retire.DEAD_CHECKOUT_QUIET_SECONDS", 0)
    return {**slot, "admin": admin}


def test_given_an_interrupted_never_started_home_when_retired_then_it_is_anchored_and_removed(interrupted):
    repo, wt = interrupted["repo"], interrupted["wt"]
    tip = _ref(repo, f"refs/heads/{BRANCH}")

    code, out, err = _retire(repo, "-y")
    assert code == 0, out + err
    assert "interrupted checkout" in out and "--force --force" in out
    assert not wt.exists()
    assert not interrupted["admin"].exists()
    assert f"worktree {wt.as_posix()}\n" not in _git("worktree", "list", "--porcelain", cwd=repo)
    assert _ref(repo, f"{ANCHORS}/{BRANCH}") == tip == _ref(repo, f"refs/heads/{BRANCH}")
    assert str(wt) not in _registry_paths()
    (row,) = (interrupted["state"] / "session-retirements.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(row)["anchors"] == {f"{ANCHORS}/{BRANCH}": tip}


def test_given_an_interrupted_home_missing_files_when_retired_then_it_is_removed(interrupted):
    (interrupted["wt"] / "README.md").unlink()
    code, out, err = _retire(interrupted["repo"], "-y")
    assert code == 0, out + err
    assert not interrupted["wt"].exists()


def test_given_a_lock_reason_other_than_initializing_when_retiring_then_it_is_refused_naming_the_lock(interrupted):
    (interrupted["admin"] / "locked").write_text("kept on purpose\n")
    err = _refused(interrupted, "-y")
    assert "lock reason is `kept on purpose`, not `initializing`" in err


def test_given_a_locked_home_with_an_index_and_another_reason_when_retiring_then_it_is_refused_before_any_write(slot):
    _git("worktree", "lock", "--reason", "on a removable drive", str(slot["wt"]), cwd=slot["repo"])
    err = _refused(slot, "-y")
    assert "lock reason is `on a removable drive`, not `initializing`" in err


def test_given_a_file_outside_heads_tree_when_retiring_an_interrupted_home_then_it_is_refused_naming_it(interrupted):
    (interrupted["wt"] / "debug.log").write_text("scratch\n")
    err = _refused(interrupted, "-y")
    assert "debug.log is not in HEAD's tree" in err


def test_given_a_file_diverging_from_heads_blob_when_retiring_an_interrupted_home_then_it_is_refused(interrupted):
    (interrupted["wt"] / "README.md").write_bytes(b"hX")
    err = _refused(interrupted, "-y")
    assert "README.md is neither HEAD's blob nor a prefix of it" in err


def test_given_a_file_longer_than_heads_blob_when_retiring_an_interrupted_home_then_it_is_refused(interrupted):
    (interrupted["wt"] / "README.md").write_bytes(b"hi\nmore\n")
    err = _refused(interrupted, "-y")
    assert "README.md is neither HEAD's blob nor a prefix of it" in err


def test_given_an_admin_dir_changed_moments_ago_when_retiring_an_interrupted_home_then_it_is_refused(
    interrupted, monkeypatch
):
    monkeypatch.setattr("ai_cli.session_retire.DEAD_CHECKOUT_QUIET_SECONDS", 3600)
    err = _refused(interrupted, "-y")
    assert "admin dir" in err and "changed" in err and "3600s" in err


def test_given_a_head_not_contained_in_the_target_when_retiring_an_interrupted_home_then_it_is_refused(interrupted):
    repo = interrupted["repo"]
    tree = _git("rev-parse", f"refs/heads/{BRANCH}^{{tree}}", cwd=repo).strip()
    local = _git("commit-tree", tree, "-p", f"refs/heads/{BRANCH}", "-m", "local only", cwd=repo).strip()
    _git("update-ref", f"refs/heads/{BRANCH}", local, cwd=repo)
    err = _refused(interrupted, "-y")
    assert f"HEAD {local[:12]} is not contained in" in err


def test_given_a_live_process_working_in_an_interrupted_home_when_retiring_then_it_is_refused_naming_it(interrupted):
    resident = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], cwd=interrupted["wt"])
    try:
        err = _refused(interrupted, "-y")
    finally:
        resident.kill()
        resident.wait()
    assert f"process {resident.pid}" in err and "inside the checkout" in err


class _UnreadableProcess:
    """A process whose working directory the OS will not disclose; by default this user's, started just now."""

    pid = 999_999

    def __init__(self, *, started: float | None = None, other_user: bool = False, exe: str | None = None) -> None:
        self.started = time.time() if started is None else started
        self.other_user = other_user
        self.executable = exe

    def oneshot(self):
        return contextlib.nullcontext()

    def create_time(self):
        return self.started

    def username(self):
        return "someone-else" if self.other_user else psutil.Process().username()

    def uids(self):
        euid = os.geteuid() + 1 if self.other_user else os.geteuid()
        return types.SimpleNamespace(real=euid, effective=euid, saved=euid)

    def cwd(self):
        raise psutil.AccessDenied(self.pid)

    def exe(self):
        if self.executable is None:
            raise psutil.AccessDenied(self.pid)
        return self.executable

    open_files = memory_maps = cmdline = cwd


def _only_process(monkeypatch, proc: _UnreadableProcess) -> None:
    monkeypatch.setattr("ai_cli.session_retire.psutil.process_iter", lambda: iter([proc]))


def test_given_a_process_whose_details_cannot_be_read_when_retiring_an_interrupted_home_then_it_is_refused(
    interrupted, monkeypatch
):
    _only_process(monkeypatch, _UnreadableProcess())
    err = _refused(interrupted, "-y")
    assert f"process {_UnreadableProcess.pid} cannot be read" in err


def test_given_an_unreadable_process_older_than_the_checkout_when_retiring_an_interrupted_home_then_it_is_removed(
    interrupted, monkeypatch
):
    _only_process(monkeypatch, _UnreadableProcess(started=0.0))
    code, out, err = _retire(interrupted["repo"], "-y")
    assert code == 0, out + err
    assert not interrupted["wt"].exists()


def test_given_an_unreadable_process_of_another_user_when_retiring_an_interrupted_home_then_it_is_removed(
    interrupted, monkeypatch
):
    _only_process(monkeypatch, _UnreadableProcess(other_user=True))
    code, out, err = _retire(interrupted["repo"], "-y")
    assert code == 0, out + err
    assert not interrupted["wt"].exists()


@pytest.mark.skipif(sys.platform == "win32", reason="the OS executable directories are POSIX paths")
def test_given_an_unreadable_os_agent_of_this_user_when_retiring_an_interrupted_home_then_it_is_removed(
    interrupted, monkeypatch
):
    _only_process(monkeypatch, _UnreadableProcess(exe="/usr/libexec/someagent"))
    code, out, err = _retire(interrupted["repo"], "-y")
    assert code == 0, out + err
    assert not interrupted["wt"].exists()


def test_given_an_unreadable_process_running_a_user_executable_when_retiring_an_interrupted_home_then_it_is_refused(
    interrupted, monkeypatch
):
    _only_process(monkeypatch, _UnreadableProcess(exe=str(interrupted["repo"].parent / "bin" / "tool")))
    err = _refused(interrupted, "-y")
    assert f"process {_UnreadableProcess.pid} cannot be read" in err
