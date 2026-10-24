"""Relaunch attaches to the live background session, not a stale resume.

Every test drives ``ai internal resolve-background-target`` through its real entry
point. ``claude agents --json`` and ``tmux capture-pane`` are answered by a stand-in
for ``subprocess.run`` (the suite forbids spawning a real ``claude``); the resolver
command is a real script, run as a subprocess exactly as the supervisor runs it.
"""

import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.main import _handle_internal

# The fake executables are POSIX shell scripts, and the supervisor that calls the
# command only runs under tmux, which Windows does not provide.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="tmux and sh are POSIX-only")

LIVE_ID = "aaaa1111"
OTHER_ID = "bbbb2222"
THIRD_ID = "cccc3333"
SESSION = "myproject-1"


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _row(kind: str, row_id: str | None, cwd: Path, name: str = SESSION, status: str | None = None) -> dict:
    row = {"kind": kind, "name": name, "cwd": str(cwd), "state": "blocked"}
    if row_id is not None:
        row["id"] = row_id
    if status is not None:
        row["status"] = status
    return row


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def fake_tools(monkeypatch: pytest.MonkeyPatch):
    """Stand in for the two system boundaries, ``claude agents --json`` and ``tmux capture-pane``.

    The suite forbids spawning a real ``claude``, so those two commands are answered here.
    Everything else, notably the resolver command under test, runs as a real subprocess.
    Returns a function that sets the agents list; if it is never called, ``claude`` exits 1.
    """
    state: dict = {"rows": None, "tmux": []}
    real_run = subprocess.run

    def run(command, *args, **kwargs):
        program = Path(str(command[0])).name
        if program == "claude" and list(command[1:]) == ["agents", "--json"]:
            rows = state["rows"]() if callable(state["rows"]) else state["rows"]
            if rows is None:
                return subprocess.CompletedProcess(command, 1, "", "no agents list")
            return subprocess.CompletedProcess(command, 0, json.dumps(rows), "")
        if program == "tmux" and command[1] in ("load-buffer", "paste-buffer", "send-keys"):
            text = Path(command[-1]).read_text(encoding="utf-8") if command[1] == "load-buffer" else ""
            state["tmux"].append((command[1], text))
            return subprocess.CompletedProcess(command, 0, "", "")
        if program == "tmux" and command[1] == "capture-pane":
            # Scrollback is requested with `-S -`; the visible screen alone lacks the older lines.
            text = "scrollback line\nbackgrounded - b85c864a\n" if "-S" in command else "visible line\n"
            return subprocess.CompletedProcess(command, 0, text, "")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")

    def set_agents(rows) -> None:
        state["rows"] = rows

    set_agents.tmux = state["tmux"]
    return set_agents


def _resolve(workdir: Path, config: dict, capsys) -> tuple[str, str]:
    with patch("ai_cli.config.load_config", return_value=config), pytest.raises(SystemExit) as exc_info:
        _handle_internal(["resolve-background-target", str(workdir), SESSION, "c-myproject-1"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    return captured.out.strip(), captured.err


def _resolve_with(workdir: Path, config: dict, capsys, *flags: str) -> tuple[str, str, int]:
    with patch("ai_cli.config.load_config", return_value=config), pytest.raises(SystemExit) as exc_info:
        _handle_internal(["resolve-background-target", str(workdir), SESSION, "c-myproject-1", *flags])
    captured = capsys.readouterr()
    return captured.out.strip(), captured.err, exc_info.value.code


def _resolver_config(command: Path | str, timeout: float | None = None) -> dict:
    relaunch = {"background_session_resolver": str(command)}
    if timeout is not None:
        relaunch["background_session_resolver_timeout_seconds"] = timeout
    return {"relaunch": relaunch}


def test_given_resolver_names_a_live_background_id_when_resolving_then_attaches_to_it(
    workdir, tmp_path, fake_tools, capsys
):
    # The named session matches neither this name nor this directory: only the hint can pick it.
    fake_tools([_row("background", LIVE_ID, tmp_path / "elsewhere", name="other")])
    resolver = _script(tmp_path / "resolver", f"echo {LIVE_ID}\n")

    out, err = _resolve(workdir, _resolver_config(resolver), capsys)

    assert out == LIVE_ID
    assert err == ""


def test_given_resolver_names_a_finished_background_id_when_resolving_then_nothing_is_attached(
    workdir, tmp_path, fake_tools, capsys
):
    fake_tools([_row("background", LIVE_ID, tmp_path / "elsewhere", name="other", status="finished")])
    resolver = _script(tmp_path / "resolver", f"echo {LIVE_ID}\n")

    out, err = _resolve(workdir, _resolver_config(resolver), capsys)

    assert out == ""
    assert LIVE_ID in err
    assert "not a live background session" in err
    assert "resuming by title" in err


def test_given_resolver_names_an_id_missing_from_the_agents_list_when_resolving_then_nothing_is_attached(
    workdir, tmp_path, fake_tools, capsys
):
    fake_tools([])
    resolver = _script(tmp_path / "resolver", f"echo {LIVE_ID}\n")

    out, err = _resolve(workdir, _resolver_config(resolver), capsys)

    assert out == ""
    assert "not a live background session" in err


def test_given_resolver_fails_and_one_name_and_directory_match_when_resolving_then_attaches_to_the_match(
    workdir, fake_tools, tmp_path, capsys
):
    fake_tools([_row("background", OTHER_ID, workdir)])
    resolver = _script(tmp_path / "resolver", "exit 3\n")

    out, _ = _resolve(workdir, _resolver_config(resolver), capsys)

    assert out == OTHER_ID


def test_given_resolver_fails_and_no_match_when_resolving_then_warns_with_the_reason(
    workdir, fake_tools, tmp_path, capsys
):
    fake_tools([])
    resolver = _script(tmp_path / "resolver", "exit 3\n")

    out, err = _resolve(workdir, _resolver_config(resolver), capsys)

    assert out == ""
    assert "background resolver exited 3" in err
    assert "resuming by title" in err


def test_given_resolver_prints_two_ids_when_resolving_then_it_counts_as_no_answer(
    workdir, fake_tools, tmp_path, capsys
):
    fake_tools([_row("background", LIVE_ID, workdir), _row("background", OTHER_ID, tmp_path / "elsewhere", name="x")])
    resolver = _script(tmp_path / "resolver", f"echo {LIVE_ID}\necho {OTHER_ID}\n")

    out, _ = _resolve(workdir, _resolver_config(resolver), capsys)

    # Only the name-and-directory match is left, so it is the hint that was ignored.
    assert out == LIVE_ID


def test_given_resolver_prints_a_malformed_id_when_resolving_then_it_counts_as_no_answer(
    workdir, fake_tools, tmp_path, capsys
):
    fake_tools([])
    resolver = _script(tmp_path / "resolver", "echo not-an-id\n")

    out, err = _resolve(workdir, _resolver_config(resolver), capsys)

    assert out == ""
    assert "did not print exactly one 8-character id" in err


def test_given_resolver_runs_past_its_timeout_when_resolving_then_falls_through(workdir, fake_tools, tmp_path, capsys):
    fake_tools([_row("background", OTHER_ID, workdir)])
    # exec, so the timeout kills the sleeping process itself and nothing outlives the test.
    resolver = _script(tmp_path / "resolver", "exec sleep 30\n")

    out, _ = _resolve(workdir, _resolver_config(resolver, timeout=0.5), capsys)

    assert out == OTHER_ID


def test_given_resolver_key_unset_when_resolving_then_the_resolver_is_never_run(workdir, fake_tools, tmp_path, capsys):
    fake_tools([_row("background", OTHER_ID, workdir)])
    marker = tmp_path / "ran"
    _script(tmp_path / "resolver", f'touch "{marker}"\necho {LIVE_ID}\n')

    out, _ = _resolve(workdir, {}, capsys)

    assert out == OTHER_ID
    assert not marker.exists()


def test_given_resolver_when_resolving_then_it_gets_the_session_name_and_the_pane_with_scrollback(
    workdir, fake_tools, tmp_path, monkeypatch, capsys
):
    fake_tools([_row("background", LIVE_ID, workdir)])
    record = tmp_path / "record"
    record.mkdir()
    monkeypatch.setenv("RECORD_DIR", str(record))
    resolver = _script(
        tmp_path / "resolver",
        'printf "%s\\n" "$@" > "$RECORD_DIR/argv.txt"\n'
        'while [ $# -gt 0 ]; do [ "$1" = "--pane-capture" ] && cp "$2" "$RECORD_DIR/pane.txt"; shift; done\n'
        f"echo {LIVE_ID}\n",
    )

    _resolve(workdir, _resolver_config(resolver), capsys)

    assert (record / "argv.txt").read_text().splitlines()[:2] == ["--session", SESSION]
    assert "scrollback line" in (record / "pane.txt").read_text()


def test_given_resolver_command_with_a_home_prefix_when_resolving_then_the_prefix_is_expanded(
    workdir, fake_tools, tmp_path, monkeypatch, capsys
):
    fake_tools([_row("background", LIVE_ID, workdir)])
    monkeypatch.setenv("HOME", str(tmp_path))
    _script(tmp_path / "resolver", f"echo {LIVE_ID}\n")

    out, _ = _resolve(workdir, _resolver_config("~/resolver"), capsys)

    assert out == LIVE_ID


def test_given_one_name_and_directory_match_when_resolving_then_attaches_to_it(workdir, fake_tools, capsys):
    fake_tools([_row("background", LIVE_ID, workdir), _row("background", OTHER_ID, workdir, name="someone-else")])

    out, err = _resolve(workdir, {}, capsys)

    assert out == LIVE_ID
    assert err == ""


def test_given_several_name_and_directory_matches_when_resolving_then_nothing_is_attached_and_candidates_are_named(
    workdir, fake_tools, capsys
):
    fake_tools([_row("background", LIVE_ID, workdir), _row("background", OTHER_ID, workdir)])

    out, err = _resolve(workdir, {}, capsys)

    assert out == ""
    assert LIVE_ID in err
    assert OTHER_ID in err
    assert "resuming by title" in err


def test_given_only_an_interactive_row_with_the_same_name_when_resolving_then_nothing_is_attached(
    workdir, fake_tools, capsys
):
    fake_tools([_row("interactive", None, workdir)])

    out, err = _resolve(workdir, {}, capsys)

    assert out == ""
    assert err == ""


def test_given_finished_background_row_with_the_same_name_when_resolving_then_nothing_is_attached(
    workdir, fake_tools, capsys
):
    fake_tools([_row("background", LIVE_ID, workdir, status="finished")])

    out, err = _resolve(workdir, {}, capsys)

    assert out == ""
    assert err == ""


def test_given_no_background_rows_when_resolving_then_prints_nothing_and_stays_silent(workdir, fake_tools, capsys):
    fake_tools([])

    out, err = _resolve(workdir, {}, capsys)

    assert out == ""
    assert err == ""


def test_given_unreadable_agents_list_when_resolving_then_warns_and_prints_nothing(workdir, fake_tools, capsys):
    # fake_tools never wrote an agents file, so the fake claude exits 1.
    out, err = _resolve(workdir, {}, capsys)

    assert out == ""
    assert "claude agents --json" in err


def test_given_invalid_relaunch_config_when_resolving_then_the_resolver_is_ignored_with_a_warning(
    workdir, fake_tools, tmp_path, capsys
):
    fake_tools([])
    marker = tmp_path / "ran"
    resolver = _script(tmp_path / "resolver", f'touch "{marker}"\necho {LIVE_ID}\n')

    out, err = _resolve(workdir, _resolver_config(resolver, timeout=-1), capsys)

    assert out == ""
    assert "invalid relaunch.background_session_resolver_timeout_seconds" in err
    assert not marker.exists()


def test_given_missing_arguments_when_resolving_then_prints_usage_and_exits_one(capsys):
    with patch("ai_cli.config.load_config", return_value={}), pytest.raises(SystemExit) as exc_info:
        _handle_internal(["resolve-background-target", "/tmp"])

    assert exc_info.value.code == 1
    assert "resolve-background-target" in capsys.readouterr().err


def _live(cwd: Path, row_id: str = LIVE_ID, name: str = SESSION, status: str = "idle", state: str = "blocked") -> dict:
    return {**_row("background", row_id, cwd, name=name, status=status), "state": state}


def test_given_a_numbered_copy_beside_a_finished_one_when_resolving_then_attaches_to_the_numbered_copy(
    workdir, fake_tools, capsys
):
    """An older finished copy holds the bare name; Claude Code numbers the live one."""
    fake_tools(
        [
            _live(workdir, row_id=OTHER_ID, state="done"),
            _live(workdir, row_id=LIVE_ID, name=f"{SESSION} (2)"),
        ]
    )

    out, _err, code = _resolve_with(workdir, {}, capsys, "--resume-follows")

    assert (out, code) == (LIVE_ID, 0)


@pytest.mark.parametrize("name", [f"{SESSION} (x)", f"{SESSION}-2", f"{SESSION}2 (2)"])
def test_given_a_row_with_another_name_when_resolving_then_it_is_not_this_sessions_copy(
    workdir, fake_tools, capsys, name
):
    fake_tools([_live(workdir, name=name)])

    out, _err, code = _resolve_with(workdir, {}, capsys, "--resume-follows")

    assert (out, code) == ("", 0)


def test_given_a_resume_follows_and_several_live_copies_when_resolving_then_the_relaunch_holds(
    workdir, fake_tools, capsys
):
    """Never start a --resume while a matching live background copy exists."""
    fake_tools([_live(workdir, row_id=LIVE_ID), _live(workdir, row_id=OTHER_ID, name=f"{SESSION} (2)")])

    out, err, code = _resolve_with(workdir, {}, capsys, "--resume-follows")

    assert (out, code) == ("", 75)
    assert LIVE_ID in err and OTHER_ID in err and "holding the relaunch" in err


def test_given_no_resume_follows_and_several_live_copies_when_resolving_then_it_does_not_hold(
    workdir, fake_tools, capsys
):
    fake_tools([_live(workdir, row_id=LIVE_ID), _live(workdir, row_id=OTHER_ID, name=f"{SESSION} (2)")])

    out, err, code = _resolve_with(workdir, {}, capsys)

    assert (out, code) == ("", 0)
    assert "resuming by title instead" in err


def test_given_a_resume_follows_and_an_unreadable_list_when_resolving_then_the_relaunch_holds(
    workdir, fake_tools, capsys
):
    del fake_tools  # the list is never set, so `claude agents --json` exits 1

    out, err, code = _resolve_with(workdir, {}, capsys, "--resume-follows")

    assert (out, code) == ("", 75)
    assert "holding the relaunch" in err


def test_given_no_resume_follows_and_an_unreadable_list_when_resolving_then_a_fresh_launch_goes_ahead(
    workdir, fake_tools, capsys
):
    del fake_tools

    out, err, code = _resolve_with(workdir, {}, capsys)

    assert (out, code) == ("", 0)
    assert "resuming by title instead" in err


def test_given_a_live_copy_and_a_resume_follows_when_resolving_then_it_attaches_instead_of_resuming(
    workdir, fake_tools, capsys
):
    fake_tools([_live(workdir)])

    out, _err, code = _resolve_with(workdir, {}, capsys, "--resume-follows", "--prompted")

    assert (out, code) == (LIVE_ID, 0)


@pytest.mark.parametrize(("status", "state"), [("busy", "working"), ("busy", "blocked"), ("idle", "working")])
def test_given_a_prompt_waits_and_the_copy_is_mid_turn_when_resolving_then_the_relaunch_holds(
    workdir, fake_tools, capsys, status, state
):
    """Never type into a copy that is mid-turn."""
    fake_tools([_live(workdir, status=status, state=state)])

    out, err, code = _resolve_with(workdir, {}, capsys, "--resume-follows", "--prompted")

    assert (out, code) == ("", 75)
    assert "mid-turn" in err


def test_given_no_prompt_waits_and_the_copy_is_mid_turn_when_resolving_then_it_still_attaches(
    workdir, fake_tools, capsys
):
    fake_tools([_live(workdir, status="busy", state="working")])

    out, _err, code = _resolve_with(workdir, {}, capsys, "--resume-follows")

    assert (out, code) == (LIVE_ID, 0)


def test_given_the_copy_is_idle_when_a_prompt_waits_then_the_prompt_is_pasted_once_and_submitted(fake_tools, tmp_path):
    from ai_cli import background_attach

    fake_tools([_live(tmp_path)])
    prompt = tmp_path / "prompt"
    prompt.write_text("Resume after an update restart\nsecond line", encoding="utf-8")

    failure = background_attach.deliver_resume_prompt("c-myproject-1", LIVE_ID, prompt, sleep=lambda _seconds: None)

    assert failure is None
    assert fake_tools.tmux == [
        ("load-buffer", "Resume after an update restart\nsecond line"),
        ("paste-buffer", ""),
        ("send-keys", ""),
    ]
    assert not prompt.exists()
    assert not prompt.with_name("prompt.undelivered").exists()


def test_given_the_copy_is_mid_turn_then_idle_when_delivering_then_it_waits_for_the_idle_row(fake_tools, tmp_path):
    from ai_cli import background_attach

    answers = iter([[_live(tmp_path, status="busy", state="working")]] * 3 + [[_live(tmp_path)]])
    fake_tools(lambda: next(answers))
    prompt = tmp_path / "prompt"
    prompt.write_text("go", encoding="utf-8")
    waits: list[float] = []

    failure = background_attach.deliver_resume_prompt("c-myproject-1", LIVE_ID, prompt, sleep=waits.append)

    assert failure is None
    assert len(waits) == 3
    assert [call for call, _ in fake_tools.tmux] == ["load-buffer", "paste-buffer", "send-keys"]


def test_given_the_copy_never_goes_idle_when_delivering_then_nothing_is_typed_and_the_prompt_is_kept(
    fake_tools, tmp_path
):
    from ai_cli import background_attach

    fake_tools([_live(tmp_path, status="busy", state="working")])
    prompt = tmp_path / "prompt"
    prompt.write_text("go", encoding="utf-8")

    failure = background_attach.deliver_resume_prompt(
        "c-myproject-1", LIVE_ID, prompt, wait_seconds=0, sleep=lambda _seconds: None
    )

    assert failure is not None and "never became idle" in failure
    assert fake_tools.tmux == []  # nothing typed into a mid-turn copy
    assert not prompt.exists()  # a later relaunch cannot replay it beside the live copy
    assert prompt.with_name("prompt.undelivered").read_text(encoding="utf-8") == "go"
    assert "prompt.undelivered" in failure


def test_given_the_copy_is_gone_when_delivering_then_nothing_is_typed(fake_tools, tmp_path):
    from ai_cli import background_attach

    fake_tools([])
    prompt = tmp_path / "prompt"
    prompt.write_text("go", encoding="utf-8")

    failure = background_attach.deliver_resume_prompt(
        "c-myproject-1", LIVE_ID, prompt, wait_seconds=0, sleep=lambda _seconds: None
    )

    assert failure is not None and "no longer listed" in failure
    assert fake_tools.tmux == []
