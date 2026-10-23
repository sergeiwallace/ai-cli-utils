"""``ai retire-session NAME`` retires a dead session's home slot, losing no commit.

Every test drives the real command against a temp repo with an ``origin`` remote and
a registered slot ``.worktrees/myrepo-3`` whose branch ``wt-myrepo-3`` is contained in
``origin/main``. The live arms (a Claude Code process, a tmux session, a fresh
heartbeat) and the content arms (uncommitted, untracked, unpushed) each refuse with
nothing written; the dead arm anchors, removes and records.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import run_cli, snapshot_tree

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
