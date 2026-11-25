"""Antigravity adoption and launch previews under a disposable HOME."""

import shlex
from types import SimpleNamespace

import portalocker
import pytest
from conftest import run_cli
from test_agy_session import CONV_1, CONV_2, _seed_agy_home

from ai_cli import agy_adopt, agy_session, config, name_registry, session, session_registry, session_script, tmux_binary
from ai_cli.name_sqlite import SqliteRegistry

TITLE = "myrepo-tst-ada"
TARGET = f"g-r-{TITLE}"


@pytest.fixture
def adoption(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = tmp_path / "myrepo"
    repo.mkdir()
    destination = repo / ".worktrees" / TITLE
    creation = []
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    monkeypatch.setattr(session_registry, "_local_session_alive", lambda _: False)
    monkeypatch.setattr(agy_adopt.psutil, "process_iter", lambda: iter(()))
    monkeypatch.setattr(agy_session, "_detect_version", lambda _: "1.5.14")

    registry = SqliteRegistry(tmp_path / "name-registry" / "names.sqlite3")
    monkeypatch.setattr(name_registry, "discover", lambda: name_registry.Discovery(registry))
    monkeypatch.setattr(name_registry, "common_words", lambda *_, **__: frozenset())
    monkeypatch.setattr("ai_cli.session_naming.registered_repo_alias", lambda _: "myrepo")
    monkeypatch.setattr("ai_cli.session_naming.resolve_host_alias", lambda: "tst")
    monkeypatch.setattr("ai_cli.session_naming.host_id", lambda: "test-host")
    monkeypatch.setattr(session, "detect_repo_root", lambda: repo)
    monkeypatch.setattr(session, "registered_worktrees", lambda _: [destination] if destination.exists() else [])

    def create(name, *, with_status, repo_root):
        assert (name, repo_root, with_status) == (TITLE, repo, True)
        creation.append(name)
        destination.mkdir(parents=True)
        return destination, True

    monkeypatch.setattr(session, "create_worktree", create)
    source = tmp_path / "original workspace"
    source.mkdir()
    store = _seed_agy_home(
        home,
        db_rows=[(CONV_1, [str(source)], "manual conversation"), (CONV_2, [str(source)], "another conversation")],
        cache={str(source): CONV_1},
        conv_files=[CONV_1, CONV_2],
    )
    record = config.get_xdg_state_home() / "sessions" / f"{TARGET}.agy.json"
    args = ["ai", "adopt-session", TITLE, "-e", "agy", "-R", "-r", str(repo)]
    return SimpleNamespace(
        args=args,
        creation=creation,
        destination=destination,
        record=record,
        registry=registry,
        repo=repo,
        source=source,
        store=store,
    )


def _files(directory):
    return {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()}


def test_given_another_workspace_when_adopted_then_exact_slot_resumes_and_retry_changes_no_files(adoption):
    native_files = _files(adoption.store)
    args = [*adoption.args, "-u", CONV_1, "-y"]
    code, out, err = run_cli(args)
    assert code == 0, out + err
    assert adoption.creation == [TITLE]
    assert agy_session.bound_slot_conversation(adoption.record, adoption.destination, TARGET) == CONV_1
    assert adoption.registry.inspect().rows["ada"]["title"] == TITLE
    assert _files(adoption.store) == native_files
    original = adoption.record.read_bytes()
    code, out, err = run_cli(args)
    assert code == 0, out + err
    assert "Already adopted" in out
    assert adoption.record.read_bytes() == original
    assert adoption.creation == [TITLE]
    assert _files(adoption.store) == native_files


def test_given_unregistered_slot_when_previewed_then_no_store_registry_or_worktree_changes(adoption):
    original = _files(adoption.store)
    code, out, err = run_cli([*adoption.args, "-u", CONV_1, "-n"])
    assert code == 0, out + err
    assert f"create worktree {adoption.destination}" in out
    assert "attach-session" in out
    assert not adoption.registry.path.exists()
    assert not adoption.record.parent.exists()
    assert not adoption.destination.exists()
    assert _files(adoption.store) == original


@pytest.mark.parametrize("reported_by", ["process", "tmux"])
def test_given_a_live_conversation_when_adoption_runs_then_it_refuses_before_writing(
    adoption, monkeypatch, reported_by
):
    if reported_by == "process":
        process = SimpleNamespace(
            pid=42, name=lambda: "agy", status=lambda: "sleeping", cmdline=lambda: ["agy", f"--conversation={CONV_1}"]
        )
        monkeypatch.setattr(agy_adopt.psutil, "process_iter", lambda: iter([process]))
        expected = "stop Antigravity process 42"
    else:
        monkeypatch.setattr(session_registry, "_local_session_alive", lambda target: target == TARGET)
        expected = f"stop tmux session {TARGET}"
    code, out, err = run_cli([*adoption.args, "-u", CONV_1, "-S", "-y"])
    assert code != 0
    assert expected in out + err
    assert not adoption.registry.path.exists()
    assert not adoption.record.exists()
    assert not adoption.destination.exists()


def test_given_multiple_conversations_at_the_process_directory_when_discovering_then_it_refuses(adoption, monkeypatch):
    process = SimpleNamespace(
        name=lambda: "agy", status=lambda: "sleeping", cmdline=lambda: ["agy"], cwd=lambda: str(adoption.source)
    )
    monkeypatch.setattr(agy_adopt.psutil, "Process", lambda _: process)
    code, out, err = run_cli([*adoption.args, "-P", "42", "-y"])
    assert code != 0
    assert "discovery matched 2 conversations" in out + err
    assert not adoption.registry.path.exists()
    assert not adoption.record.exists()


def test_given_native_presence_lock_is_held_when_adopting_then_it_refuses_even_with_stopped(adoption):
    lock = adoption.store / "presence" / f"{CONV_1}.lock"
    lock.parent.mkdir()
    with lock.open("wb") as handle:
        portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        code, out, err = run_cli([*adoption.args, "-u", CONV_1, "-S", "-y"])
        assert code != 0
        assert "still holds its presence lock" in out + err
    assert not adoption.registry.path.exists()
    assert not adoption.record.exists()


def test_given_adopted_slot_when_launch_is_previewed_then_it_prints_the_supervisor_pane_command(adoption, monkeypatch):
    code, out, err = run_cli([*adoption.args, "-u", CONV_1, "-y"])
    assert code == 0, out + err
    monkeypatch.setattr("ai_cli.main._resolve_session_identity", lambda *_, **__: (TARGET, TITLE))
    monkeypatch.setattr(session, "_resolve_is_remote", lambda *_, **__: True)
    monkeypatch.setattr(session, "_tmux_session_names", list)
    monkeypatch.setattr(session, "get_project_prefix", lambda: "myrepo")
    monkeypatch.setattr(session, "is_current_project_resolved", lambda: True)
    monkeypatch.setattr(config, "validate_registry_completeness", lambda *_, **__: True)
    monkeypatch.setattr(config, "resolve_project_prefix", lambda *_, **__: "myrepo")
    monkeypatch.setattr("ai_cli.tmux_setup.tmux_present", lambda: True)
    monkeypatch.setattr(config, "_find_project_dir", lambda _: adoption.repo)
    monkeypatch.chdir(adoption.repo)
    extra = "--dangerously-skip-permissions"
    code, preview, err = run_cli(["ai", "g", TITLE, "-p", "myrepo", "-d", "--", extra])
    assert code == 0, preview + err
    assert "attach-session" in preview
    launch_prefix = tuple(tmux_binary.argv("new-session"))
    line = next(line for line in preview.splitlines() if tuple(shlex.split(line)[:2]) == launch_prefix)
    assert line in out.splitlines()
    commands = shlex.split(line)
    assert commands[commands.index("-c") + 1] == str(adoption.destination)
    pane_command = commands[commands.index("&&") - 1]
    supervisor = session_script.get_engine_script(
        "g",
        TITLE,
        TARGET,
        "g-r-myrepo-tst-",
        "myrepo",
        "",
        False,
        str(adoption.destination),
        is_remote=True,
        resume=True,
        agy_args=[extra],
    )
    rendered = next(
        s.strip()
        for s in supervisor.splitlines()
        if s.strip().startswith("run_agent ") and extra in s and not s.strip().startswith("run_agent env ")
    )
    rendered = rendered.removeprefix("run_agent ").removesuffix(' "$@"')
    substitutions = {
        "$_ai_state_dir/sessions/$tmux_session.agy.json": str(adoption.record),
        "$PWD": str(adoption.destination),
        "$tmux_session": TARGET,
        "$ai_name": TITLE,
    }
    assert shlex.split(pane_command) == ["exec", *(substitutions.get(word, word) for word in shlex.split(rendered))]
