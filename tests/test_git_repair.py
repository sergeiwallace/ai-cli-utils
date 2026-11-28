"""Tests for the core.bare=true / stale core.worktree corruption fix (AI-CLI-99),
plus the phantom-deletion detection guards."""

import contextlib
import http.server
import os
import subprocess
import threading
from unittest.mock import MagicMock, patch

import pytest

from ai_cli.git_repair import (
    _GIT_TARGETING_VARS,
    GitProbeError,
    _git_env,
    detect_missing_tracked_symlinks,
    detect_phantom_deleted_files,
    operation_in_progress,
    pull_rebase_autostash,
    repair_bare_worktree_config,
    stash_entries,
    sync_worktree_branch,
    unmerged_paths,
)

# --- _git_env ---


def test_git_env_when_targeting_vars_present_then_strips_them():
    base = {
        "GIT_DIR": "/tmp/somewhere/.git",
        "GIT_WORK_TREE": "/tmp/somewhere",
        "GIT_INDEX_FILE": "/tmp/somewhere/.git/index",
        "GIT_OBJECT_DIRECTORY": "/tmp/somewhere/.git/objects",
        "GIT_COMMON_DIR": "/tmp/somewhere/.git",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/tmp/other/objects",
        "GIT_PREFIX": "sub/dir/",
        "GIT_CONFIG": "/tmp/somewhere/.git/config",
        "GIT_CONFIG_GLOBAL": "/tmp/home/.gitconfig",
        "PATH": "/usr/bin",
    }

    result = _git_env(base)

    for var in _GIT_TARGETING_VARS:
        assert var not in result
    assert result["PATH"] == "/usr/bin"


def test_git_env_when_innocuous_git_vars_present_then_keeps_them():
    base = {
        "GIT_DIR": "/tmp/somewhere/.git",
        "GIT_SSH": "/usr/bin/ssh",
        "GIT_SSH_COMMAND": "ssh -o foo=bar",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    result = _git_env(base)

    assert "GIT_DIR" not in result
    assert result["GIT_SSH"] == "/usr/bin/ssh"
    assert result["GIT_SSH_COMMAND"] == "ssh -o foo=bar"
    assert result["GIT_TERMINAL_PROMPT"] == "0"
    assert result["GIT_AUTHOR_NAME"] == "Test"
    assert result["GIT_AUTHOR_EMAIL"] == "t@t"
    assert result["GIT_COMMITTER_NAME"] == "Test"
    assert result["GIT_COMMITTER_EMAIL"] == "t@t"


def test_git_env_when_no_base_given_then_scrubs_os_environ():
    with patch.dict("os.environ", {"GIT_DIR": "/leaked", "SOME_VAR": "1"}, clear=False):
        result = _git_env()
    assert "GIT_DIR" not in result
    assert result.get("SOME_VAR") == "1"


# --- credential-prompt containment (`ai c` stalled on "Username for 'https://...'") ---
#
# git writes its credential prompt to /dev/tty, NOT to the subprocess's stdin or
# stdout, so `capture_output=True` does not contain it. Every git subprocess this
# package runs is unattended automation with no one to answer, so a prompt can
# only ever hang the caller -- which is exactly what stalled `ai c <session>` on a
# machine whose git had no usable credential for the worktree's https remote.


def test_git_env_when_caller_left_prompt_unset_then_real_git_refuses_to_prompt(tmp_path):
    """Drives the real `git` CLI, because the defect IS git's prompting behaviour:
    a mocked subprocess would only assert what the mock was told to say."""
    base = {k: v for k, v in os.environ.items() if k != "GIT_TERMINAL_PROMPT"}

    result = subprocess.run(
        # An empty `credential.helper` resets the helper list, so the only route
        # left to git is its own terminal prompt -- the mechanism under test, and
        # the same on every OS regardless of which helper is installed.
        ["git", "-c", "credential.helper=", "credential", "fill"],
        input="protocol=https\nhost=git.example\n\n",
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_git_env(base),
        check=False,
    )

    assert result.returncode != 0
    assert "terminal prompts disabled" in result.stderr, result.stderr


def test_git_env_when_caller_set_prompt_explicitly_then_value_is_not_overridden():
    """The containment is a default, not a clamp: a caller that deliberately wants
    an interactive git keeps it."""
    assert _git_env({"GIT_TERMINAL_PROMPT": "1"})["GIT_TERMINAL_PROMPT"] == "1"


# --- repair_bare_worktree_config ---


def test_repair_when_bare_true_then_resets_false_and_reports_repaired(tmp_path):
    def mock_run(cmd, **kwargs):
        if "core.bare" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="true\n")
        if "core.worktree" in cmd and "--get" in cmd:
            return MagicMock(returncode=1, stdout="")
        return MagicMock(returncode=0, stdout="")

    with patch("subprocess.run", side_effect=mock_run) as mock_subprocess:
        result = repair_bare_worktree_config(tmp_path)

    assert result is True
    set_calls = [c.args[0] for c in mock_subprocess.call_args_list if "config" in c.args[0] and "false" in c.args[0]]
    assert any("core.bare" in c for c in set_calls)


def test_repair_when_stale_worktree_config_then_unsets_and_reports_repaired(tmp_path):
    def mock_run(cmd, **kwargs):
        if "core.bare" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="false\n")
        if "core.worktree" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="/some/sibling/.worktrees/hub-37\n")
        return MagicMock(returncode=0, stdout="")

    with patch("subprocess.run", side_effect=mock_run) as mock_subprocess:
        result = repair_bare_worktree_config(tmp_path)

    assert result is True
    unset_calls = [c.args[0] for c in mock_subprocess.call_args_list if "--unset" in c.args[0]]
    assert any("core.worktree" in c for c in unset_calls)


def test_repair_when_both_bare_true_and_stale_worktree_then_repairs_both(tmp_path):
    def mock_run(cmd, **kwargs):
        if "core.bare" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="true\n")
        if "core.worktree" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="/some/sibling/.worktrees/hub-37\n")
        return MagicMock(returncode=0, stdout="")

    with patch("subprocess.run", side_effect=mock_run) as mock_subprocess:
        result = repair_bare_worktree_config(tmp_path)

    assert result is True
    calls = [c.args[0] for c in mock_subprocess.call_args_list]
    assert any("core.bare" in c and "false" in c for c in calls)
    assert any("--unset" in c and "core.worktree" in c for c in calls)


def test_repair_when_healthy_repo_then_noop_and_returns_false(tmp_path):
    def mock_run(cmd, **kwargs):
        if "core.bare" in cmd and "--get" in cmd:
            return MagicMock(returncode=0, stdout="false\n")
        if "core.worktree" in cmd and "--get" in cmd:
            return MagicMock(returncode=1, stdout="")
        return MagicMock(returncode=0, stdout="")

    with patch("subprocess.run", side_effect=mock_run) as mock_subprocess:
        result = repair_bare_worktree_config(tmp_path)

    assert result is False
    set_calls = [c.args[0] for c in mock_subprocess.call_args_list if "false" in c.args[0] or "--unset" in c.args[0]]
    assert set_calls == []


def test_repair_when_real_repo_corrupted_then_fixes_it_end_to_end(tmp_path):
    """Integration-style repro: a real git repo flipped to core.bare=true (± a
    stale core.worktree) is restored to a normal working tree by the guard."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
    )

    # Simulate the corruption directly (the documented failure mode): flip
    # core.bare=true and point core.worktree at a sibling worktree directory
    # (the sibling dir must exist — git rejects config ops that reference a
    # nonexistent core.worktree path with "fatal: Invalid path").
    sibling_worktree = tmp_path / ".worktrees" / "hub-37"
    sibling_worktree.mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "config", "--local", "core.bare", "true"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "core.worktree", str(sibling_worktree)],
        check=True,
    )

    result = repair_bare_worktree_config(repo)

    assert result is True
    bare = subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "--get", "core.bare"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bare.stdout.strip() == "false"
    worktree_cfg = subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "--get", "core.worktree"],
        capture_output=True,
        check=False,
    )
    assert worktree_cfg.returncode != 0  # unset


# --- pull_rebase_autostash / unmerged_paths / stash_entries (Shape B) ---
#
# These drive real `git` subprocesses on real repos on purpose. The defect IS
# git's exit-code behaviour, so a mocked subprocess would assert only what the
# mock was told to say and would pass just as happily against the broken code.


def _git(repo, *args, **kwargs):
    return subprocess.run(["git", "-C", str(repo), *list(args)], capture_output=True, text=True, check=False, **kwargs)


def _make_remote(path):
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "f.txt").write_text("line1\n")
    _git(path, "add", "f.txt")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _clone(remote, local):
    # --no-local: a path clone copies the source's objects/ file by file, and on a macOS
    # runner it died with ENOENT on a `.tmp-*-pack-*.rev` that vanished from the source
    # mid-copy (AI-CLI-99pc). The pack protocol reads no transient file.
    subprocess.run(["git", "clone", "-q", "--no-local", str(remote), str(local)], check=True)
    _git(local, "config", "user.email", "t@t")
    _git(local, "config", "user.name", "t")
    return local


def _remote_edits_same_line(remote):
    (remote / "f.txt").write_text("remote-change\n")
    _git(remote, "commit", "-q", "-am", "remote edits same line")


def test_pull_rebase_autostash_when_pop_conflicts_then_reports_strand_despite_exit_zero(tmp_path):
    """The defect itself: a same-line local-vs-remote edit makes the autostash
    pop conflict, yet `git pull --rebase --autostash` still exits 0. Measured on
    git 2.43.0 and 2.55.0. A caller gating on `returncode != 0` sees success and
    walks into a conflicted index — which stranded a remote build host 50 commits
    behind for five days."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (local / "f.txt").write_text("local-change\n")  # uncommitted, never staged
    _remote_edits_same_line(remote)

    pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode == 0, "if this ever becomes non-zero, git fixed the defect and the guard can be simplified"
    assert stranded is not None, "the guard must see the strand the exit code hides"
    assert "f.txt" in stranded
    # And the repo really is stranded, exactly as the live specimen was:
    assert unmerged_paths(local) == {"f.txt"}
    assert len(stash_entries(local)) == 1
    assert operation_in_progress(local) is None  # nothing "in progress" to signal it


class _AlwaysUnauthorized(http.server.BaseHTTPRequestHandler):
    """Smallest remote that makes git ask for a credential: answer every request 401."""

    def do_GET(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="git"')
        self.end_headers()

    def log_message(self, *args):
        pass  # keep the captured test output readable


@contextlib.contextmanager
def _remote_demanding_credentials():
    server = http.server.HTTPServer(("127.0.0.1", 0), _AlwaysUnauthorized)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/repo.git"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_pull_rebase_autostash_when_remote_demands_credentials_then_fails_without_prompting(tmp_path):
    """The reported defect, at the function `ai c` actually calls to sync a worktree.
    A loopback remote that 401s every request is the real protocol boundary: git
    reaches the credential step for real, so nothing here can pass by stubbing it.
    Unattended, git must fail fast and leave the repo usable -- prompting instead
    blocks the launcher on `Username for '...'` forever and no session ever starts."""
    local = _make_remote(tmp_path / "local")
    with _remote_demanding_credentials() as url:
        _git(local, "remote", "add", "origin", url)
        # Reset the helper list so the machine's configured helper cannot answer
        # (or, on Windows, raise a GUI prompt) and mask the behaviour under test.
        _git(local, "config", "--local", "credential.helper", "")

        pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode != 0
    assert "terminal prompts disabled" in (pull.stderr + pull.stdout), pull.stderr
    # ...and a credential failure must stay a soft failure: the caller refuses the
    # launch on a strand, and being unable to reach the remote is not one.
    assert stranded is None
    assert unmerged_paths(local) == set()


def test_pull_rebase_autostash_when_clean_pull_then_no_strand(tmp_path):
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (remote / "g.txt").write_text("unrelated\n")
    _git(remote, "add", "g.txt")
    _git(remote, "commit", "-q", "-m", "unrelated remote change")

    pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode == 0
    assert stranded is None
    assert unmerged_paths(local) == set()


_MOCK_HEAD = "c0ffee"


def _own_autostash_records(stash_ids: str) -> str:
    """Render ``stash list`` records in which every id is this worktree's own autostash."""
    return "".join(f"{stash_id}\0autostash\0On main: autostash\0{_MOCK_HEAD} f00d\n" for stash_id in stash_ids.split())


def test_given_beads_issues_only_strand_when_pull_completes_then_regenerates_and_clears_it(tmp_path, capsys):
    """The generated issues mirror is the one safe exception to the strand refusal."""
    repo = tmp_path / "repo"
    repo.mkdir()
    new_stash = "new-autostash"
    old_stash = "pre-existing-wip"
    calls: list[tuple[list[str], dict]] = []
    unmerged_results = ["", "100644 deadbeef 1\t.beads/issues.jsonl\0"]
    stash_results = [f"{old_stash}\n", f"{old_stash}\n{new_stash}\n"]

    def mock_run(cmd, **kwargs):
        command = list(cmd)
        calls.append((command, kwargs))
        if command[3:] == ["ls-files", "--unmerged", "-z"]:
            return MagicMock(returncode=0, stdout=unmerged_results.pop(0), stderr="")
        if command[3:] == ["stash", "list", "--format=%H"]:
            return MagicMock(returncode=0, stdout=stash_results.pop(0), stderr="")
        if command[3:] == ["stash", "list", "--format=%H%x00%gs%x00%s%x00%P"]:
            return MagicMock(returncode=0, stdout=_own_autostash_records(stash_results.pop(0)), stderr="")
        if command[3:] == ["rev-parse", "--verify", "-q", "HEAD"]:
            return MagicMock(returncode=0, stdout=f"{_MOCK_HEAD}\n", stderr="")
        if command[3:] == ["symbolic-ref", "-q", "HEAD"]:
            return MagicMock(returncode=0, stdout="refs/heads/main\n", stderr="")
        if command[3:] == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return MagicMock(returncode=0, stdout="origin/main\n", stderr="")
        if command[3:] == ["status", "--porcelain", "-uno"]:
            return MagicMock(returncode=0, stdout=" M .beads/issues.jsonl\n", stderr="")
        if command[3:] == ["pull", "--rebase", "--autostash"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if command[3:] == ["rev-parse", "--absolute-git-dir"]:
            return MagicMock(returncode=0, stdout=f"{repo / '.git'}\n", stderr="")
        if command == ["bd", "doctor"]:
            return MagicMock(returncode=0, stdout="healthy\n", stderr="")
        if command == ["bd", "export", "--output", ".beads/issues.jsonl"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if command[3:] == ["add", "--", ".beads/issues.jsonl"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if command[3:] == ["stash", "show", "--format=", "--name-only", "-z", new_stash]:
            return MagicMock(returncode=0, stdout=".beads/issues.jsonl\0", stderr="")
        if command[3:] == ["stash", "drop", new_stash]:
            return MagicMock(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess command: {command}")

    with patch("subprocess.run", side_effect=mock_run):
        pull, stranded = pull_rebase_autostash(repo)

    assert pull.returncode == 0
    assert stranded is None
    commands = [command for command, _ in calls]
    assert ["bd", "doctor"] in commands
    assert ["bd", "export", "--output", ".beads/issues.jsonl"] in commands
    assert ["git", "-C", str(repo), "add", "--", ".beads/issues.jsonl"] in commands
    assert ["git", "-C", str(repo), "stash", "drop", new_stash] in commands
    assert all(old_stash not in command for command in commands)
    assert f"dropped stash {new_stash}" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("new_paths", "doctor_returncode", "expected_reason"),
    [
        (
            {".beads/issues.jsonl", ".beads/interactions.jsonl"},
            None,
            "2 newly conflicted path(s): .beads/interactions.jsonl, .beads/issues.jsonl; "
            "1 autostash entr(y/ies) left on the stash stack (new-autostash)",
        ),
        (
            {".beads/interactions.jsonl"},
            None,
            "1 newly conflicted path(s): .beads/interactions.jsonl; "
            "1 autostash entr(y/ies) left on the stash stack (new-autostash)",
        ),
        (
            {".beads/issues.jsonl"},
            1,
            "1 newly conflicted path(s): .beads/issues.jsonl; "
            "1 autostash entr(y/ies) left on the stash stack (new-autostash)",
        ),
        (
            {".beads/issues.jsonl"},
            OSError("bd is unavailable"),
            "1 newly conflicted path(s): .beads/issues.jsonl; "
            "1 autostash entr(y/ies) left on the stash stack (new-autostash)",
        ),
    ],
)
def test_given_unsafe_beads_strand_when_pull_completes_then_retains_the_original_refusal(
    tmp_path, new_paths, doctor_returncode, expected_reason
):
    """Only the issues mirror plus a queryable store may bypass the fatal path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    new_stash = "new-autostash"
    calls: list[list[str]] = []
    unmerged_results = ["", "".join(f"100644 deadbeef 1\t{path}\0" for path in sorted(new_paths))]
    stash_results = ["", f"{new_stash}\n"]

    def mock_run(cmd, **kwargs):
        command = list(cmd)
        calls.append(command)
        if command[3:] == ["ls-files", "--unmerged", "-z"]:
            return MagicMock(returncode=0, stdout=unmerged_results.pop(0), stderr="")
        if command[3:] == ["stash", "list", "--format=%H"]:
            return MagicMock(returncode=0, stdout=stash_results.pop(0), stderr="")
        if command[3:] == ["stash", "list", "--format=%H%x00%gs%x00%s%x00%P"]:
            return MagicMock(returncode=0, stdout=_own_autostash_records(stash_results.pop(0)), stderr="")
        if command[3:] == ["rev-parse", "--verify", "-q", "HEAD"]:
            return MagicMock(returncode=0, stdout=f"{_MOCK_HEAD}\n", stderr="")
        if command[3:] == ["symbolic-ref", "-q", "HEAD"]:
            return MagicMock(returncode=0, stdout="refs/heads/main\n", stderr="")
        if command[3:] == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return MagicMock(returncode=0, stdout="origin/main\n", stderr="")
        if command[3:] == ["status", "--porcelain", "-uno"]:
            return MagicMock(returncode=0, stdout=" M .beads/issues.jsonl\n", stderr="")
        if command[3:] == ["pull", "--rebase", "--autostash"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if command[3:] == ["rev-parse", "--absolute-git-dir"]:
            return MagicMock(returncode=0, stdout=f"{repo / '.git'}\n", stderr="")
        if command == ["bd", "doctor"] and doctor_returncode is not None:
            if isinstance(doctor_returncode, OSError):
                raise doctor_returncode
            return MagicMock(returncode=doctor_returncode, stdout="", stderr="unavailable")
        raise AssertionError(f"unexpected subprocess command: {command}")

    with patch("subprocess.run", side_effect=mock_run):
        pull, stranded = pull_rebase_autostash(repo)

    assert pull.returncode == 0
    assert stranded == expected_reason
    assert ["bd", "export", "--output", ".beads/issues.jsonl"] not in calls
    assert ["git", "-C", str(repo), "add", "--", ".beads/issues.jsonl"] not in calls


def test_given_issues_only_strand_with_nonissues_autostash_when_repaired_then_retains_stash(tmp_path, capsys):
    """Resolving the generated file never requires discarding an unsafe stash."""
    repo = tmp_path / "repo"
    repo.mkdir()
    new_stash = "new-autostash"
    calls: list[list[str]] = []
    unmerged_results = ["", "100644 deadbeef 1\t.beads/issues.jsonl\0"]
    stash_results = ["", f"{new_stash}\n"]

    def mock_run(cmd, **kwargs):
        command = list(cmd)
        calls.append(command)
        if command[3:] == ["ls-files", "--unmerged", "-z"]:
            return MagicMock(returncode=0, stdout=unmerged_results.pop(0), stderr="")
        if command[3:] == ["stash", "list", "--format=%H"]:
            return MagicMock(returncode=0, stdout=stash_results.pop(0), stderr="")
        if command[3:] == ["stash", "list", "--format=%H%x00%gs%x00%s%x00%P"]:
            return MagicMock(returncode=0, stdout=_own_autostash_records(stash_results.pop(0)), stderr="")
        if command[3:] == ["rev-parse", "--verify", "-q", "HEAD"]:
            return MagicMock(returncode=0, stdout=f"{_MOCK_HEAD}\n", stderr="")
        if command[3:] == ["symbolic-ref", "-q", "HEAD"]:
            return MagicMock(returncode=0, stdout="refs/heads/main\n", stderr="")
        if command[3:] == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return MagicMock(returncode=0, stdout="origin/main\n", stderr="")
        if command[3:] == ["status", "--porcelain", "-uno"]:
            return MagicMock(returncode=0, stdout=" M .beads/issues.jsonl\n", stderr="")
        if command[3:] == ["pull", "--rebase", "--autostash"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if command[3:] == ["rev-parse", "--absolute-git-dir"]:
            return MagicMock(returncode=0, stdout=f"{repo / '.git'}\n", stderr="")
        if command == ["bd", "doctor"]:
            return MagicMock(returncode=0, stdout="healthy\n", stderr="")
        if command == ["bd", "export", "--output", ".beads/issues.jsonl"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if command[3:] == ["add", "--", ".beads/issues.jsonl"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if command[3:] == ["stash", "show", "--format=", "--name-only", "-z", new_stash]:
            return MagicMock(returncode=0, stdout=".beads/interactions.jsonl\0", stderr="")
        raise AssertionError(f"unexpected subprocess command: {command}")

    with patch("subprocess.run", side_effect=mock_run):
        pull, stranded = pull_rebase_autostash(repo)

    assert pull.returncode == 0
    assert stranded is None
    assert ["git", "-C", str(repo), "stash", "drop", new_stash] not in calls
    assert "left newly created stash entries in place" in capsys.readouterr().err


def test_pull_rebase_autostash_when_repo_holds_unrelated_wip_stashes_then_clean_pull_is_not_flagged(tmp_path):
    """Regression for the false positive that made the previous guard useless:
    it reported ANY stash entry as a strand. The stranded host's main tree held
    three unrelated WIP stashes, so that guard would have cried wolf on every
    clean launch — and a warning that always fires is a warning nobody reads."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (local / "f.txt").write_text("my real work in progress\n")
    _git(local, "stash", "push", "-q", "-m", "WIP on main: real work")
    before = stash_entries(local)
    assert len(before) == 1

    (remote / "g.txt").write_text("unrelated\n")
    _git(remote, "add", "g.txt")
    _git(remote, "commit", "-q", "-m", "unrelated remote change")

    pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode == 0
    assert stranded is None, "a pre-existing WIP stash is not this pull's doing"
    assert stash_entries(local) == before, "and it must be left untouched"


@contextlib.contextmanager
def _sibling_worktree_stashes_during_pull(local, sibling, message):
    """Push a named stash from another worktree of the same repo while the pull runs.

    The stash stack is shared by every worktree of a repository, so a slow pull on
    a busy host can see entries that another session pushed mid-pull. Running the
    sibling's stash right after the real pull returns lands it inside that window
    deterministically.
    """
    _git(local, "worktree", "add", "-q", "-b", "sibling", str(sibling))
    _git(sibling, "config", "user.email", "t@t")
    _git(sibling, "config", "user.name", "t")
    real_run = subprocess.run

    def run(cmd, *args, **kwargs):
        result = real_run(cmd, *args, **kwargs)
        if list(cmd[3:5]) == ["pull", "--rebase"]:
            (sibling / "f.txt").write_text("sibling work in progress\n")
            pushed = real_run(
                ["git", "-C", str(sibling), "stash", "push", "-q", "-m", message], capture_output=True, text=True
            )
            assert pushed.returncode == 0, pushed.stderr
        return result

    with patch("subprocess.run", side_effect=run):
        yield


def test_pull_rebase_autostash_when_sibling_worktree_stashes_during_clean_pull_then_no_strand(tmp_path):
    """Another worktree's named stash, pushed while this pull runs, is not this
    pull's autostash. Counting it refused a launch into a clean, up-to-date tree."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (remote / "g.txt").write_text("unrelated\n")
    _git(remote, "add", "g.txt")
    _git(remote, "commit", "-q", "-m", "unrelated remote change")

    with _sibling_worktree_stashes_during_pull(local, tmp_path / "sibling", "sibling wip"):
        pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode == 0
    assert len(stash_entries(local)) == 1, "the sibling's stash really landed during the pull"
    assert stranded is None, "a stash another worktree pushed is not this pull's strand"


def test_pull_rebase_autostash_when_sibling_stashes_during_stranding_pull_then_counts_only_own_autostash(tmp_path):
    """A genuine strand is still refused, and only this pull's own autostash is counted."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (local / "f.txt").write_text("local-change\n")
    _remote_edits_same_line(remote)

    with _sibling_worktree_stashes_during_pull(local, tmp_path / "sibling", "sibling wip"):
        pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode == 0
    assert len(stash_entries(local)) == 2
    assert stranded is not None
    assert "1 autostash entr(y/ies) left on the stash stack" in stranded


def test_pull_rebase_autostash_when_detached_head_pop_conflicts_then_still_reports_own_autostash(tmp_path):
    """Attribution must not fail open on a detached HEAD, where git labels the entry "(no branch)"."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    _git(local, "checkout", "-q", "--detach")
    (local / "f.txt").write_text("local-change\n")
    _remote_edits_same_line(remote)
    _git(local, "fetch", "-q", "origin")
    with patch("ai_cli.git_repair._pull_refspec", return_value=["origin", "main"]):
        pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode == 0
    assert len(stash_entries(local)) == 1
    assert stranded is not None
    assert "1 autostash entr(y/ies) left on the stash stack" in stranded


def test_pull_rebase_autostash_when_sibling_stash_is_named_autostash_then_not_attributed(tmp_path):
    """A user stash whose message happens to be "autostash" is still not git's autostash:
    the stash reflog records it as "On <branch>: autostash", not the bare word git writes."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")

    with _sibling_worktree_stashes_during_pull(local, tmp_path / "sibling", "autostash"):
        pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode == 0
    assert len(stash_entries(local)) == 1
    assert stranded is None


def test_pull_rebase_autostash_when_index_already_conflicted_then_strand_not_attributed_to_this_pull(tmp_path):
    """An already-conflicted repo is not re-blamed on the next pull. Git refuses
    to pull at all here (non-zero), and the caller decides what that means —
    `ai update` treats any conflicted index as fatal, `ai c N` only refuses when
    the launch itself caused it."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (local / "f.txt").write_text("local-change\n")
    _remote_edits_same_line(remote)
    _, first = pull_rebase_autostash(local)
    assert first is not None  # strand it

    (remote / "g.txt").write_text("more upstream work\n")
    _git(remote, "add", "g.txt")
    _git(remote, "commit", "-q", "-m", "more upstream")

    pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode != 0, "git refuses to pull onto an unmerged index"
    assert stranded is None, "the conflict predates this pull"
    assert unmerged_paths(local) == {"f.txt"}, "caller can still see the repo is unusable"


def test_pull_rebase_autostash_when_remote_unreachable_then_fails_without_stranding(tmp_path):
    """A pull that merely fails leaves the tree intact — that is not a strand,
    and callers must stay usable offline."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    _git(local, "remote", "set-url", "origin", str(tmp_path / "does-not-exist"))

    pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode != 0
    assert stranded is None
    assert unmerged_paths(local) == set()


def test_unmerged_paths_when_index_has_conflict_stages_then_returns_those_paths(tmp_path):
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (local / "f.txt").write_text("local-change\n")
    _remote_edits_same_line(remote)
    assert unmerged_paths(local) == set()

    pull_rebase_autostash(local)

    assert unmerged_paths(local) == {"f.txt"}


def test_stash_entries_when_entries_pushed_then_returns_their_ids(tmp_path):
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    assert stash_entries(local) == set()

    for i in range(3):
        (local / "f.txt").write_text(f"wip {i}\n")
        _git(local, "stash", "push", "-q", "-m", f"wip {i}")

    entries = stash_entries(local)
    assert len(entries) == 3
    assert all(len(sha) == 40 for sha in entries)


def test_stash_entries_when_one_dropped_and_one_added_then_identity_not_count_decides(tmp_path):
    """Identities rather than a count: a concurrent drop can hold the count flat
    while a genuinely new entry appeared. A count-based guard would see 0 growth
    and report nothing."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (local / "f.txt").write_text("old wip\n")
    _git(local, "stash", "push", "-q", "-m", "old")
    before = stash_entries(local)

    _git(local, "stash", "drop", "-q", "stash@{0}")
    (local / "f.txt").write_text("new wip\n")
    _git(local, "stash", "push", "-q", "-m", "new")
    after = stash_entries(local)

    assert len(after) == len(before), "count is unchanged — this is what fools a counter"
    assert after != before
    assert after - before, "identity comparison still sees the new entry"


def test_pull_rebase_autostash_when_rebase_body_conflicts_then_not_reported_as_strand(tmp_path):
    """A plain rebase conflict is NOT this defect: git exits non-zero and leaves
    `rebase-merge` behind, so it already told the user. Reporting it here would
    refuse the session that could resolve it and give stash advice for a pull
    that never stashed anything."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (local / "f.txt").write_text("local-committed\n")
    _git(local, "commit", "-q", "-am", "local commit same line")  # committed, so no autostash
    _remote_edits_same_line(remote)

    pull, stranded = pull_rebase_autostash(local)

    assert pull.returncode != 0, "git reports this one honestly"
    assert unmerged_paths(local), "and it does leave conflict stages"
    assert operation_in_progress(local) == "rebase-merge"
    assert stranded is None, "an in-progress rebase is visible by ordinary means"


def test_operation_in_progress_when_repo_clean_then_none(tmp_path):
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")

    assert operation_in_progress(local) is None


def test_unmerged_paths_when_path_has_spaces_and_non_ascii_then_decoded_intact(tmp_path):
    """The probe backs a fatal gate, so a parsing miss is a silent pass."""
    awkward = "dir with spaces/файл 'quoted'.txt"
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-q", "-b", "main")
    _git(remote, "config", "user.email", "t@t")
    _git(remote, "config", "user.name", "t")
    (remote / "dir with spaces").mkdir()
    (remote / awkward).write_text("line1\n")
    _git(remote, "add", "-A")
    _git(remote, "commit", "-q", "-m", "init")

    local = _clone(remote, tmp_path / "local")
    (local / awkward).write_text("local-change\n")
    (remote / awkward).write_text("remote-change\n")
    _git(remote, "commit", "-q", "-am", "remote edits same line")

    _, stranded = pull_rebase_autostash(local)

    assert unmerged_paths(local) == {awkward}
    assert stranded is not None and awkward in stranded


def test_unmerged_paths_when_probe_fails_then_raises_rather_than_reporting_clean(tmp_path):
    """Fail closed. If this returned an empty set on error, "could not look" and
    "looked, it was clean" would be the same value — which is how a gate goes
    inert."""
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    with pytest.raises(GitProbeError):
        unmerged_paths(not_a_repo)

    with pytest.raises(GitProbeError):
        stash_entries(not_a_repo)


def test_pull_rebase_autostash_when_state_unverifiable_then_reports_strand(tmp_path):
    """An unverifiable repo is treated as unsafe rather than assumed clean."""
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    _, stranded = pull_rebase_autostash(not_a_repo)

    assert stranded is not None
    assert "could not be verified" in stranded


# --- detect_missing_tracked_symlinks (Shape A) ---


def test_detect_missing_tracked_symlinks_when_symlink_missing_from_disk_then_reports_it(tmp_path):
    """Reproduces Shape A's exact signature: a tracked symlink HEAD still
    lists is absent from disk (verified `os.path.lexists` failure), with no git
    error anywhere — the same shape a Claude Code `isolation: worktree` checkout
    produced for 21 real symlinks in one sub-agent worktree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "docs").mkdir()
    (repo / "docs" / "real.md").write_text("real content\n")
    link = repo / "docs" / "link.md"
    link.symlink_to("real.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init with a tracked symlink")

    assert detect_missing_tracked_symlinks(repo) == []  # sanity: present and tracked

    # Simulate the checkout dropping the symlink from disk (Shape A) — the index/
    # HEAD are untouched, exactly matching `git status` reporting a deletion
    # nobody made while `git show HEAD:<path>` still succeeds.
    link.unlink()

    missing = detect_missing_tracked_symlinks(repo)

    assert missing == ["docs/link.md"]
    show = _git(repo, "show", "HEAD:docs/link.md")
    assert show.returncode == 0  # still present in HEAD — a real "phantom" deletion


def test_detect_missing_tracked_symlinks_when_regular_file_missing_then_ignored(tmp_path):
    """A missing REGULAR file (Shape B's signature, not Shape A's) must not be
    reported here — the two shapes are confirmed distinct root causes and this
    detector is scoped to the symlink-specific mechanism only."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("content\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    (repo / "f.txt").unlink()

    assert detect_missing_tracked_symlinks(repo) == []


# --- detect_phantom_deleted_files (Shape C) ---


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "docs").mkdir()
    (repo / "docs" / "plan.md").write_text("plan content\n" * 40)
    (repo / "keep.txt").write_text("untouched\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _precommit_staged_files_only_cycle(repo):
    """Replay pre-commit's `staged_files_only` exactly, without needing pre-commit.

    Mirrors `pre_commit/staged_files_only.py::_unstaged_changes_cleared`: capture
    the unstaged diff as a binary patch, `git checkout -- .` to clear the working
    tree, then re-apply the patch in the `finally:` block. Returns the patch bytes
    so a test can assert what pre-commit would have persisted to its cache.
    """
    tree = _git(repo, "write-tree").stdout.strip()
    diff = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff-index",
            "--ignore-submodules",
            "--binary",
            "--exit-code",
            "--no-color",
            "--no-ext-diff",
            tree,
            "--",
        ],
        capture_output=True,
        check=False,
    )
    if diff.returncode == 0:
        return b""
    patch_file = repo.parent / "patch-capture"
    patch_file.write_bytes(diff.stdout)
    _git(repo, "-c", "submodule.recurse=0", "checkout", "--", ".")
    _git(repo, "apply", "--whitespace=nowarn", str(patch_file))
    return diff.stdout


def test_detect_phantom_deleted_files_when_tracked_regular_file_missing_then_reports_it(tmp_path):
    """Shape C's exact signature: the index still holds the blob, HEAD still has it,
    but the file is gone from disk — so `git status` shows a deletion nobody made and
    committing would remove content still live on the remote."""
    repo = _init_repo(tmp_path)

    assert detect_phantom_deleted_files(repo) == []  # sanity: healthy tree is silent

    (repo / "docs" / "plan.md").unlink()

    assert detect_phantom_deleted_files(repo) == ["docs/plan.md"]
    assert _git(repo, "show", "HEAD:docs/plan.md").returncode == 0  # content still safe


def test_detect_phantom_deleted_files_when_worktree_clean_then_empty(tmp_path):
    """Negative control: the detector must be silent on a healthy tree, otherwise
    firing on the sick one proves nothing."""
    repo = _init_repo(tmp_path)

    assert detect_phantom_deleted_files(repo) == []


def test_detect_phantom_deleted_files_when_file_modified_not_deleted_then_ignored(tmp_path):
    """An ordinary unstaged edit is not a phantom deletion and must not warn."""
    repo = _init_repo(tmp_path)

    (repo / "docs" / "plan.md").write_text("edited\n")

    assert detect_phantom_deleted_files(repo) == []


def test_detect_phantom_deleted_files_when_missing_entry_is_symlink_then_ignored(tmp_path):
    """Shape A stays with its own detector: a missing tracked symlink has a different
    cause and a different fix, so reporting it here would double-warn."""
    repo = _init_repo(tmp_path)
    link = repo / "docs" / "link.md"
    link.symlink_to("plan.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add tracked symlink")

    link.unlink()

    assert detect_phantom_deleted_files(repo) == []
    assert detect_missing_tracked_symlinks(repo) == ["docs/link.md"]


def test_shape_b_guard_when_phantom_deletion_present_then_blind_to_it(tmp_path):
    """Why Shape C needed a new detector at all: pre-commit stashes to its OWN patch
    file under ~/.cache/pre-commit, never `git stash`, so the Shape B guard sees an
    empty stash list and an unconflicted index, and stays silent on a genuinely
    broken worktree."""
    repo = _init_repo(tmp_path)
    (repo / "docs" / "plan.md").unlink()

    # The blindness, reproduced against the Shape B guard's actual inputs.
    assert stash_entries(repo) == set()
    assert unmerged_paths(repo) == set()
    assert detect_phantom_deleted_files(repo) == ["docs/plan.md"]


def test_detect_phantom_deleted_files_when_precommit_cycle_runs_then_deletion_survives(tmp_path):
    """The perpetuation mechanism, reproduced end to end.

    pre-commit's `staged_files_only` captures the lone deletion as a patch, runs
    `git checkout -- .` (which RESTORES the file — the very fix), then re-applies the
    patch, deleting it again. The tree ends byte-identical to how it started and
    nothing errors, which is why this shape survived every pull and every hook run on
    `myproject/.worktrees/myproject-1` for over 24 hours.
    """
    repo = _init_repo(tmp_path)
    (repo / "docs" / "plan.md").unlink()

    patch_bytes = _precommit_staged_files_only_cycle(repo)

    assert b"deleted file mode 100644" in patch_bytes  # what lands in the patch cache
    assert not (repo / "docs" / "plan.md").exists()  # restored, then re-deleted
    assert detect_phantom_deleted_files(repo) == ["docs/plan.md"]
    assert _git(repo, "stash", "list").stdout.strip() == ""  # no stash, ever


def test_detect_phantom_deleted_files_when_restored_then_precommit_cycle_is_a_noop(tmp_path):
    """`git checkout -- <path>` is a durable fix: with a clean tree pre-commit's
    `git diff-index` returns 0, so no patch is captured and nothing gets replayed."""
    repo = _init_repo(tmp_path)
    (repo / "docs" / "plan.md").unlink()

    _git(repo, "checkout", "--", "docs/plan.md")
    patch_bytes = _precommit_staged_files_only_cycle(repo)

    assert patch_bytes == b""
    assert (repo / "docs" / "plan.md").exists()
    assert detect_phantom_deleted_files(repo) == []


def _make_bare_remote_with_main(tmp_path):
    """Create a bare remote with one commit on main and return (remote, seed)."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = _clone(remote, tmp_path / "seed")
    _git(seed, "checkout", "-q", "-b", "main")
    (seed / "base.txt").write_text("base\n", encoding="utf-8")
    _git(seed, "add", "base.txt")
    _git(seed, "commit", "-q", "-m", "initial commit")
    _git(seed, "push", "-q", "-u", "origin", "main")
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    return remote, seed


def _advance_remote_main(seed, filename="main-advance.txt", content="from main\n"):
    """Commit and push a new commit on remote main, returning its SHA."""
    _git(seed, "checkout", "-q", "main")
    (seed / filename).write_text(content, encoding="utf-8")
    _git(seed, "add", filename)
    _git(seed, "commit", "-q", "-m", f"advance main with {filename}")
    _git(seed, "push", "-q", "origin", "main")
    return _git(seed, "rev-parse", "HEAD").stdout.strip()


def test_given_pushed_pr_branch_and_remote_main_ahead_when_syncing_report_mode_then_tip_unchanged_and_remote_ref_updated(
    tmp_path,
):
    """AC-1 + AC-4: default report mode fetches without rebasing a published branch."""
    remote, seed = _make_bare_remote_with_main(tmp_path)
    local = _clone(remote, tmp_path / "local")
    _git(local, "checkout", "-q", "-b", "feat/open-pr")
    (local / "feature.txt").write_text("pr work\n", encoding="utf-8")
    _git(local, "add", "feature.txt")
    _git(local, "commit", "-q", "-m", "pr commit")
    _git(local, "push", "-q", "-u", "origin", "feat/open-pr")
    pr_tip = _git(local, "rev-parse", "HEAD").stdout.strip()

    new_main_sha = _advance_remote_main(seed)

    sync = sync_worktree_branch(local, published_mode="report")

    assert sync.arm == "published-report"
    assert sync.process.returncode == 0
    assert sync.stranded is None
    assert _git(local, "rev-parse", "HEAD").stdout.strip() == pr_tip
    assert _git(local, "rev-parse", "origin/main").stdout.strip() == new_main_sha
    assert "skipped rebase" in sync.summary
    assert "1 commit(s) behind origin/main" in sync.summary


def test_given_pushed_branch_tracking_origin_main_when_syncing_then_skips_rebase_and_updates_remote_ref(tmp_path):
    """AC-1: a session branch pushed without -u (still tracking origin/main) is also protected."""
    remote, seed = _make_bare_remote_with_main(tmp_path)
    local = _clone(remote, tmp_path / "local")
    _git(local, "checkout", "-q", "--track", "-b", "wt/session-1/my-pr", "origin/main")
    (local / "work.txt").write_text("published work\n", encoding="utf-8")
    _git(local, "add", "work.txt")
    _git(local, "commit", "-q", "-m", "session pr commit")
    _git(local, "push", "-q", "origin", "HEAD")
    pr_tip = _git(local, "rev-parse", "HEAD").stdout.strip()

    new_main_sha = _advance_remote_main(seed)

    sync = sync_worktree_branch(local, published_mode="report")

    assert sync.arm == "published-report"
    assert _git(local, "rev-parse", "HEAD").stdout.strip() == pr_tip
    assert _git(local, "rev-parse", "origin/main").stdout.strip() == new_main_sha


def test_given_pushed_pr_branch_when_syncing_merge_mode_then_merges_main_preserving_published_history(tmp_path):
    """AC-1: merge mode merges origin/main while keeping the published PR tip as an ancestor."""
    remote, seed = _make_bare_remote_with_main(tmp_path)
    local = _clone(remote, tmp_path / "local")
    _git(local, "checkout", "-q", "-b", "feat/merge-mode")
    (local / "feature.txt").write_text("pr work\n", encoding="utf-8")
    _git(local, "add", "feature.txt")
    _git(local, "commit", "-q", "-m", "pr commit")
    _git(local, "push", "-q", "-u", "origin", "feat/merge-mode")
    pr_tip = _git(local, "rev-parse", "HEAD").stdout.strip()

    new_main_sha = _advance_remote_main(seed)

    sync = sync_worktree_branch(local, published_mode="merge")

    assert sync.arm == "published-merge"
    assert sync.process.returncode == 0
    assert _git(local, "merge-base", "--is-ancestor", pr_tip, "HEAD").returncode == 0
    assert _git(local, "merge-base", "--is-ancestor", new_main_sha, "HEAD").returncode == 0
    assert (local / "main-advance.txt").exists()
    assert "merged 1 commit(s) from origin/main" in sync.summary


def test_given_unpushed_session_branch_when_syncing_then_rebases_and_reports_arm(tmp_path):
    """AC-2: an unpushed branch still rebases onto origin/main and names the arm it took."""
    remote, seed = _make_bare_remote_with_main(tmp_path)
    local = _clone(remote, tmp_path / "local")
    _git(local, "checkout", "-q", "--track", "-b", "wt-session-1", "origin/main")
    (local / "local.txt").write_text("unpushed\n", encoding="utf-8")
    _git(local, "add", "local.txt")
    _git(local, "commit", "-q", "-m", "unpushed local commit")
    old_tip = _git(local, "rev-parse", "HEAD").stdout.strip()

    new_main_sha = _advance_remote_main(seed)

    sync = sync_worktree_branch(local, published_mode="report")

    assert sync.arm == "rebase"
    assert sync.process.returncode == 0
    assert _git(local, "rev-parse", "HEAD").stdout.strip() != old_tip
    assert _git(local, "merge-base", "--is-ancestor", new_main_sha, "HEAD").returncode == 0
    assert "synced branch 'wt-session-1' by pull --rebase" in sync.summary


def test_given_diverged_remote_branch_when_syncing_then_rebases_and_reports_arm(tmp_path):
    """AC-2: when upstream IS origin/<branch> and not an ancestor of HEAD, pull --rebase runs."""
    remote, seed = _make_bare_remote_with_main(tmp_path)
    local = _clone(remote, tmp_path / "local")
    _git(local, "checkout", "-q", "-b", "feat/diverged")
    (local / "local.txt").write_text("from local\n", encoding="utf-8")
    _git(local, "add", "local.txt")
    _git(local, "commit", "-q", "-m", "initial branch commit")
    _git(local, "push", "-q", "-u", "origin", "feat/diverged")

    _git(seed, "fetch", "-q", "origin")
    _git(seed, "checkout", "-q", "-b", "feat/diverged", "origin/feat/diverged")
    (seed / "peer.txt").write_text("from peer\n", encoding="utf-8")
    _git(seed, "add", "peer.txt")
    _git(seed, "commit", "-q", "-m", "peer commit on remote branch")
    _git(seed, "push", "-q", "origin", "feat/diverged")
    peer_sha = _git(seed, "rev-parse", "HEAD").stdout.strip()

    sync = sync_worktree_branch(local, published_mode="report")

    assert sync.arm == "rebase"
    assert sync.process.returncode == 0
    assert _git(local, "merge-base", "--is-ancestor", peer_sha, "HEAD").returncode == 0
    assert "synced branch 'feat/diverged' by pull --rebase" in sync.summary
    assert "not an ancestor of HEAD" in sync.summary


def test_given_diverged_pushed_branch_tracking_origin_main_when_syncing_then_skips_rebase_and_reports_divergence(
    tmp_path,
):
    """A pushed branch tracking origin/main that diverged from origin/<branch> must not be rebased again."""
    remote, seed = _make_bare_remote_with_main(tmp_path)
    local = _clone(remote, tmp_path / "local")
    _git(local, "checkout", "-q", "--track", "-b", "wt/session-1/amended-pr", "origin/main")
    (local / "work.txt").write_text("v1\n", encoding="utf-8")
    _git(local, "add", "work.txt")
    _git(local, "commit", "-q", "-m", "initial pr commit")
    _git(local, "push", "-q", "origin", "HEAD")

    (local / "work.txt").write_text("v2\n", encoding="utf-8")
    _git(local, "commit", "-q", "--amend", "-am", "amended pr commit")
    amended_tip = _git(local, "rev-parse", "HEAD").stdout.strip()

    new_main_sha = _advance_remote_main(seed)

    sync = sync_worktree_branch(local, published_mode="report")

    assert sync.arm == "published-diverged"
    assert sync.process.returncode == 0
    assert _git(local, "rev-parse", "HEAD").stdout.strip() == amended_tip
    assert _git(local, "rev-parse", "origin/main").stdout.strip() == new_main_sha
    assert "diverged from 'origin/wt/session-1/amended-pr' (1 ahead / 1 behind)" in sync.summary
    assert "skipped rebase" in sync.summary


def test_given_unpublished_stacked_branch_when_syncing_then_not_misclassified_as_published(tmp_path):
    """An unpublished branch tracking origin/feat-a must not take the published-branch arm."""
    remote, seed = _make_bare_remote_with_main(tmp_path)
    _git(seed, "checkout", "-q", "-b", "feat/base")
    (seed / "base-feat.txt").write_text("base feature\n", encoding="utf-8")
    _git(seed, "add", "base-feat.txt")
    _git(seed, "commit", "-q", "-m", "base feature")
    _git(seed, "push", "-q", "-u", "origin", "feat/base")

    local = _clone(remote, tmp_path / "local")
    _git(local, "checkout", "-q", "--track", "-b", "feat/stacked", "origin/feat/base")
    (local / "stacked.txt").write_text("stacked work\n", encoding="utf-8")
    _git(local, "add", "stacked.txt")
    _git(local, "commit", "-q", "-m", "stacked commit")

    _git(seed, "checkout", "-q", "feat/base")
    (seed / "base-feat-2.txt").write_text("base feature 2\n", encoding="utf-8")
    _git(seed, "add", "base-feat-2.txt")
    _git(seed, "commit", "-q", "-m", "base feature update")
    _git(seed, "push", "-q", "origin", "feat/base")
    new_base_sha = _git(seed, "rev-parse", "HEAD").stdout.strip()

    sync = sync_worktree_branch(local, published_mode="merge")

    assert sync.arm == "rebase"
    assert sync.process.returncode == 0
    assert _git(local, "merge-base", "--is-ancestor", new_base_sha, "HEAD").returncode == 0
    assert "synced branch 'feat/stacked' by pull --rebase" in sync.summary


def test_given_clean_vs_dirty_tree_when_pull_rebase_autostash_runs_then_autostash_used_only_when_dirty(tmp_path):
    """AC-3: --autostash is passed only when the worktree has tracked modifications."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    real_run = subprocess.run
    observed_pull_cmds: list[list[str]] = []

    def spy_run(cmd, *args, **kwargs):
        command = [str(part) for part in cmd]
        if "pull" in command:
            observed_pull_cmds.append(command)
        return real_run(cmd, *args, **kwargs)

    with patch("ai_cli.git_repair.subprocess.run", side_effect=spy_run):
        pull_rebase_autostash(local)
        (local / "f.txt").write_text("dirty edit\n", encoding="utf-8")
        pull_rebase_autostash(local)

    assert len(observed_pull_cmds) == 2
    assert "--autostash" not in observed_pull_cmds[0]
    assert "--autostash" in observed_pull_cmds[1]


def test_given_stranded_autostash_pop_when_pull_completes_then_reports_stranded_stash_sha(tmp_path):
    """AC-3: a stranded autostash entry is reported by its commit SHA."""
    remote = _make_remote(tmp_path / "remote")
    local = _clone(remote, tmp_path / "local")
    (local / "f.txt").write_text("local-change\n", encoding="utf-8")
    _remote_edits_same_line(remote)

    _pull, stranded = pull_rebase_autostash(local)

    assert stranded is not None
    stashes = stash_entries(local)
    assert len(stashes) == 1
    stash_sha = next(iter(stashes))
    assert stash_sha in stranded


def test_given_pushed_branch_when_session_launches_then_tip_unchanged_and_banner_reports_distance(tmp_path, capsys):
    """AC-4: end-to-end _do_session_launch on a real git repo leaves a pushed branch tip intact."""
    from ai_cli.main import _do_session_launch

    remote, seed = _make_bare_remote_with_main(tmp_path)
    worktree = _clone(remote, tmp_path / "worktree")
    _git(worktree, "checkout", "-q", "-b", "feat/launched-pr")
    (worktree / "feature.txt").write_text("pr content\n", encoding="utf-8")
    _git(worktree, "add", "feature.txt")
    _git(worktree, "commit", "-q", "-m", "pr commit")
    _git(worktree, "push", "-q", "-u", "origin", "feat/launched-pr")
    pr_tip = _git(worktree, "rev-parse", "HEAD").stdout.strip()

    new_main_sha = _advance_remote_main(seed)

    with (
        patch("ai_cli.main._direnv_setup.ensure_direnv", return_value=MagicMock(installed=True, detail="")),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.trust.ensure_workspace_trusted"),
        patch("ai_cli.session.build_session_name", return_value=("c-myproject-1", "myproject-1")),
        patch("ai_cli.session.detect_repo_root", return_value=None),
        patch("ai_cli.session.create_worktree", return_value=(worktree, False)),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.main._exec_with_direnv", side_effect=SystemExit(0)),
    ):
        with pytest.raises(SystemExit) as exc_info:
            _do_session_launch(
                engine="c",
                name="1",
                resume=False,
                once=False,
                bare=True,
                notify=False,
                sandbox=False,
                no_worktree=False,
                remote=False,
                project="",
                is_remote=False,
                project_prefix_override="myproject",
                extra_args=[],
                config={"worktree": {"enabled": True}},
            )

    assert exc_info.value.code == 0
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == pr_tip
    assert _git(worktree, "rev-parse", "origin/main").stdout.strip() == new_main_sha
    err = capsys.readouterr().err
    assert "skipped rebase" in err
    assert "1 commit(s) behind origin/main" in err


def test_given_resolved_staged_merge_when_launch_syncs_published_merge_mode_then_preserves_merge_head(tmp_path, capsys):
    """P1 #1: launch must not run git merge --abort on a user's resolved, staged, uncommitted merge."""
    from ai_cli.main import _do_session_launch

    remote, seed = _make_bare_remote_with_main(tmp_path)
    worktree = _clone(remote, tmp_path / "worktree")
    _git(worktree, "checkout", "-q", "-b", "feat/resolved-merge")
    (worktree / "base.txt").write_text("feature version\n", encoding="utf-8")
    _git(worktree, "commit", "-q", "-am", "feature edit")
    _git(worktree, "push", "-q", "-u", "origin", "feat/resolved-merge")

    _advance_remote_main(seed, filename="base.txt", content="main version\n")
    _git(worktree, "fetch", "-q", "origin")
    conflict_merge = _git(worktree, "merge", "--no-commit", "origin/main")
    assert conflict_merge.returncode != 0
    (worktree / "base.txt").write_text("resolved version\n", encoding="utf-8")
    _git(worktree, "add", "base.txt")
    assert unmerged_paths(worktree) == set()
    assert operation_in_progress(worktree) == "MERGE_HEAD"

    _advance_remote_main(seed, filename="extra.txt", content="second main advance\n")

    with (
        patch("ai_cli.main._direnv_setup.ensure_direnv", return_value=MagicMock(installed=True, detail="")),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        patch("ai_cli.trust.ensure_workspace_trusted"),
        patch("ai_cli.session.build_session_name", return_value=("c-myproject-1", "myproject-1")),
        patch("ai_cli.session.detect_repo_root", return_value=None),
        patch("ai_cli.session.create_worktree", return_value=(worktree, False)),
        patch("ai_cli.config.get_current_project_name", return_value="myproject"),
        patch("ai_cli.config.get_worktree_published_branch_sync", return_value="merge"),
        patch("ai_cli.main._exec_with_direnv", side_effect=SystemExit(0)),
    ):
        with pytest.raises(SystemExit) as exc_info:
            _do_session_launch(
                engine="c",
                name="1",
                resume=False,
                once=False,
                bare=True,
                notify=False,
                sandbox=False,
                no_worktree=False,
                remote=False,
                project="",
                is_remote=False,
                project_prefix_override="myproject",
                extra_args=[],
                config={"worktree": {"enabled": True, "published_branch_sync": "merge"}},
            )

    assert exc_info.value.code == 0
    assert operation_in_progress(worktree) == "MERGE_HEAD"
    assert (worktree / "base.txt").read_text(encoding="utf-8") == "resolved version\n"
    assert "merge with origin/main failed" in capsys.readouterr().err


def test_given_merge_autostash_pop_conflict_when_syncing_published_merge_mode_then_reports_stranded_sha(tmp_path):
    """P1 #2: published merge mode must detect and report a stranded merge.autoStash pop by SHA."""
    remote, seed = _make_bare_remote_with_main(tmp_path)
    local = _clone(remote, tmp_path / "local")
    _git(local, "checkout", "-q", "-b", "feat/merge-autostash")
    (local / "feature.txt").write_text("pr file\n", encoding="utf-8")
    _git(local, "add", "feature.txt")
    _git(local, "commit", "-q", "-m", "pr commit")
    _git(local, "push", "-q", "-u", "origin", "feat/merge-autostash")
    _git(local, "config", "merge.autoStash", "true")

    _advance_remote_main(seed, filename="base.txt", content="remote-change\n")
    (local / "base.txt").write_text("local-dirty-change\n", encoding="utf-8")

    sync = sync_worktree_branch(local, published_mode="merge")

    assert sync.arm == "published-merge"
    assert sync.stranded is not None
    assert "base.txt" in sync.stranded
    stashes = stash_entries(local)
    assert len(stashes) == 1
    assert next(iter(stashes)) in sync.stranded


def test_given_branch_pushed_to_separate_push_remote_when_syncing_then_skips_rebase_and_updates_origin(tmp_path):
    """P1 #3: a branch pushed to a separate pushRemote (fork) must not be rebased when origin/main advances."""
    origin, seed = _make_bare_remote_with_main(tmp_path)
    fork = tmp_path / "fork.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(fork)], check=True)

    local = _clone(origin, tmp_path / "local")
    _git(local, "remote", "add", "fork", str(fork))
    _git(local, "checkout", "-q", "--track", "-b", "feat/fork-pr", "origin/main")
    _git(local, "config", "branch.feat/fork-pr.pushRemote", "fork")
    (local / "fork-work.txt").write_text("fork pr\n", encoding="utf-8")
    _git(local, "add", "fork-work.txt")
    _git(local, "commit", "-q", "-m", "fork pr commit")
    _git(local, "push", "-q", "fork", "HEAD")
    pr_tip = _git(local, "rev-parse", "HEAD").stdout.strip()

    new_main_sha = _advance_remote_main(seed)

    sync = sync_worktree_branch(local, published_mode="report")

    assert sync.arm == "published-report"
    assert sync.process.returncode == 0
    assert _git(local, "rev-parse", "HEAD").stdout.strip() == pr_tip
    assert _git(local, "rev-parse", "origin/main").stdout.strip() == new_main_sha
    assert "tracks 'fork/feat/fork-pr'" in sync.summary
    assert "1 commit(s) behind origin/main" in sync.summary


def test_given_remote_branch_without_local_tracking_ref_when_syncing_then_probes_remote_and_skips_rebase(tmp_path):
    """P2 #4: an existing remote branch with no cached local tracking ref must not be rebased."""
    remote, seed = _make_bare_remote_with_main(tmp_path)
    local = _clone(remote, tmp_path / "local")
    _git(local, "config", "--replace-all", "remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main")
    _git(local, "checkout", "-q", "--track", "-b", "feat/single-branch-pr", "origin/main")
    (local / "work.txt").write_text("published work\n", encoding="utf-8")
    _git(local, "add", "work.txt")
    _git(local, "commit", "-q", "-m", "published commit")
    _git(local, "push", "-q", "origin", "HEAD")
    _git(local, "update-ref", "-d", "refs/remotes/origin/feat/single-branch-pr")
    assert _git(local, "rev-parse", "--verify", "-q", "refs/remotes/origin/feat/single-branch-pr").returncode != 0
    pr_tip = _git(local, "rev-parse", "HEAD").stdout.strip()

    new_main_sha = _advance_remote_main(seed)

    sync = sync_worktree_branch(local, published_mode="report")

    assert sync.arm == "published-report"
    assert sync.process.returncode == 0
    assert _git(local, "rev-parse", "HEAD").stdout.strip() == pr_tip
    assert _git(local, "rev-parse", "origin/main").stdout.strip() == new_main_sha
    assert "skipped rebase" in sync.summary


def test_given_pushed_branch_behind_own_remote_when_tracking_origin_main_then_reports_behind_not_diverged(tmp_path):
    """A pushed branch strictly behind origin/<branch> (0 ahead / N behind) says 'behind', not 'diverged'."""
    remote, seed = _make_bare_remote_with_main(tmp_path)
    local = _clone(remote, tmp_path / "local")
    _git(local, "checkout", "-q", "--track", "-b", "wt/session-1/peer-advanced", "origin/main")
    (local / "work.txt").write_text("v1\n", encoding="utf-8")
    _git(local, "add", "work.txt")
    _git(local, "commit", "-q", "-m", "v1")
    _git(local, "push", "-q", "origin", "HEAD")
    local_tip = _git(local, "rev-parse", "HEAD").stdout.strip()

    _git(seed, "fetch", "-q", "origin")
    _git(seed, "checkout", "-q", "-b", "wt/session-1/peer-advanced", "origin/wt/session-1/peer-advanced")
    (seed / "peer.txt").write_text("peer\n", encoding="utf-8")
    _git(seed, "add", "peer.txt")
    _git(seed, "commit", "-q", "-m", "peer commit")
    _git(seed, "push", "-q", "origin", "wt/session-1/peer-advanced")

    sync = sync_worktree_branch(local, published_mode="report")

    assert sync.arm == "published-diverged"
    assert _git(local, "rev-parse", "HEAD").stdout.strip() == local_tip
    assert "is 1 commit(s) behind 'origin/wt/session-1/peer-advanced'" in sync.summary
    assert "diverged" not in sync.summary


def test_given_unreachable_separate_push_remote_when_syncing_then_refuses_rebase_conservatively(tmp_path):
    """P2 #4: when a separate pushRemote cannot be probed, launch refuses to rebase onto origin."""
    origin, seed = _make_bare_remote_with_main(tmp_path)
    local = _clone(origin, tmp_path / "local")
    _git(local, "remote", "add", "fork", str(tmp_path / "unreachable-fork.git"))
    _git(local, "checkout", "-q", "--track", "-b", "feat/fork-unreachable", "origin/main")
    _git(local, "config", "branch.feat/fork-unreachable.pushRemote", "fork")
    (local / "work.txt").write_text("work\n", encoding="utf-8")
    _git(local, "add", "work.txt")
    _git(local, "commit", "-q", "-m", "local work")
    tip_before = _git(local, "rev-parse", "HEAD").stdout.strip()

    _advance_remote_main(seed)

    sync = sync_worktree_branch(local, published_mode="report")

    assert sync.arm == "published-fetch-failed"
    assert sync.process.returncode != 0
    assert _git(local, "rev-parse", "HEAD").stdout.strip() == tip_before
    assert "skipped rebase" in sync.summary


def test_given_missing_fork_ref_and_divergent_origin_upstream_when_syncing_then_probes_fork_and_skips_rebase(
    tmp_path,
):
    """P1: probe pushRemote before falling back to cached origin/<branch>, protecting fork commits."""
    origin, seed = _make_bare_remote_with_main(tmp_path)
    fork = tmp_path / "fork.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(fork)], check=True)

    _git(seed, "checkout", "-q", "-b", "feat/shared")
    (seed / "origin-commit.txt").write_text("origin v1\n", encoding="utf-8")
    _git(seed, "add", "origin-commit.txt")
    _git(seed, "commit", "-q", "-m", "origin v1")
    _git(seed, "push", "-q", "-u", "origin", "feat/shared")

    local = _clone(origin, tmp_path / "local")
    _git(local, "remote", "add", "fork", str(fork))
    _git(local, "checkout", "-q", "--track", "-b", "feat/shared", "origin/feat/shared")
    _git(local, "config", "branch.feat/shared.pushRemote", "fork")
    (local / "fork-commit.txt").write_text("published on fork\n", encoding="utf-8")
    _git(local, "add", "fork-commit.txt")
    _git(local, "commit", "-q", "-m", "fork commit L")
    _git(local, "push", "-q", "fork", "HEAD")
    _git(local, "update-ref", "-d", "refs/remotes/fork/feat/shared")
    fork_tip = _git(local, "rev-parse", "HEAD").stdout.strip()

    _git(seed, "checkout", "-q", "feat/shared")
    (seed / "origin-advance.txt").write_text("origin v2\n", encoding="utf-8")
    _git(seed, "add", "origin-advance.txt")
    _git(seed, "commit", "-q", "-m", "origin v2")
    _git(seed, "push", "-q", "origin", "feat/shared")

    sync = sync_worktree_branch(local, published_mode="report")

    assert sync.arm == "published-report"
    assert sync.process.returncode == 0
    assert _git(local, "rev-parse", "HEAD").stdout.strip() == fork_tip
    assert "tracks 'fork/feat/shared'" in sync.summary
    assert "skipped rebase" in sync.summary


def test_given_fork_with_restricted_fetch_refspec_when_origin_main_ahead_then_report_and_merge_preserve_fetched_tip(
    tmp_path,
):
    """P2: fetching origin must not clobber FETCH_HEAD before checking ancestry of fork/<branch>."""
    origin, seed = _make_bare_remote_with_main(tmp_path)
    fork = tmp_path / "fork.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(fork)], check=True)

    local = _clone(origin, tmp_path / "local")
    _git(local, "remote", "add", "fork", str(fork))
    _git(local, "config", "--replace-all", "remote.fork.fetch", "+refs/heads/main:refs/remotes/fork/main")
    _git(local, "checkout", "-q", "--track", "-b", "feat/narrow-fork", "origin/main")
    _git(local, "config", "branch.feat/narrow-fork.pushRemote", "fork")
    (local / "work.txt").write_text("fork work\n", encoding="utf-8")
    _git(local, "add", "work.txt")
    _git(local, "commit", "-q", "-m", "fork commit")
    _git(local, "push", "-q", "fork", "HEAD")
    _git(local, "update-ref", "-d", "refs/remotes/fork/feat/narrow-fork")
    fork_tip = _git(local, "rev-parse", "HEAD").stdout.strip()

    new_main_sha = _advance_remote_main(seed)

    report_sync = sync_worktree_branch(local, published_mode="report")

    assert report_sync.arm == "published-report"
    assert report_sync.process.returncode == 0
    assert _git(local, "rev-parse", "HEAD").stdout.strip() == fork_tip
    assert _git(local, "rev-parse", "origin/main").stdout.strip() == new_main_sha
    assert "1 commit(s) behind origin/main" in report_sync.summary

    merge_sync = sync_worktree_branch(local, published_mode="merge")

    assert merge_sync.arm == "published-merge"
    assert merge_sync.process.returncode == 0
    assert _git(local, "merge-base", "--is-ancestor", fork_tip, "HEAD").returncode == 0
    assert _git(local, "merge-base", "--is-ancestor", new_main_sha, "HEAD").returncode == 0
    assert "merged 1 commit(s) from origin/main" in merge_sync.summary
