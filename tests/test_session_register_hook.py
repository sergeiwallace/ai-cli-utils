"""A configured session-register command runs when a Codex or agy session starts.

A deployment may keep a registry of live agent sessions, so other tools can address a
session by name. ``[session_register] command`` names a program the session-start hook
runs, with the client (``codex`` or ``agy``) substituted for ``{client}``, once the
launch is confirmed. The hook runs inside the client's own process tree, so the program
can find the client among its ancestors. A failing program costs one warning line and
never refuses the session or changes the hook's reply. With no command configured,
nothing runs.

The register program here is a fixture that records its arguments and its ancestors.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import psutil
import pytest
from test_agy_session import CONV_1, SLOT, _seed_agy_home
from test_codex_control import slot  # noqa: F401

from ai_cli import agy_session
from ai_cli import config as ai_config

_REGISTER_TOOL = """\
import json, os, sys
from pathlib import Path
import psutil

log, mode = Path(sys.argv[1]), sys.argv[2]
ancestors = [parent.pid for parent in psutil.Process().parents()]
with log.open("a", encoding="utf-8") as out:
    out.write(json.dumps({"argv": sys.argv[3:], "ancestors": ancestors}) + "\\n")
if mode == "fail":
    print("register refused: no-engine")
    sys.exit(3)
"""


def _configure(tmp_path: Path, command: list[str] | None, mode: str = "ok") -> Path:
    """Write ``[session_register]`` into the isolated config.toml; return the call log."""
    tool = tmp_path / "register_tool.py"
    tool.write_text(_REGISTER_TOOL, encoding="utf-8")
    log = tmp_path / "register-calls.jsonl"
    config = ai_config._config_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    if command is None:
        config.write_text("", encoding="utf-8")
    else:
        argv = [sys.executable, str(tool), str(log), mode, *command]
        config.write_text(f"[session_register]\ncommand = {json.dumps(argv)}\n", encoding="utf-8")
    return log


def _calls(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _confirm_agy(tmp_path, monkeypatch, *, token_override: str | None = None) -> int:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    home = tmp_path / "home"
    _seed_agy_home(home)
    record = tmp_path / "state" / f"{SLOT}.agy.json"
    _, rec_str, token = agy_session.prepare(
        record, worktree, SLOT, fresh=True, route="local-tmux", client_version="1.5.14", home=home
    )
    monkeypatch.setenv(agy_session.SLOT_RECORD_ENV, rec_str)
    monkeypatch.setenv(agy_session.LAUNCH_TOKEN_ENV, token_override or token)
    event = {"hookEventName": "SessionStart", "conversationId": CONV_1, "cwd": str(worktree)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    return agy_session.confirm_from_environment([])


def test_given_register_command_when_agy_session_start_confirms_then_it_runs_for_agy(tmp_path, monkeypatch, capsys):
    log = _configure(tmp_path, ["register", "--client", "{client}"])

    assert _confirm_agy(tmp_path, monkeypatch) == 0

    assert capsys.readouterr().out.strip() == "{}"
    assert [call["argv"] for call in _calls(log)] == [["register", "--client", "agy"]]


def test_given_register_command_fails_when_agy_session_starts_then_session_still_confirms(
    tmp_path, monkeypatch, capsys
):
    log = _configure(tmp_path, ["register", "--client", "{client}"], mode="fail")

    assert _confirm_agy(tmp_path, monkeypatch) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == "{}"
    assert len(_calls(log)) == 1
    warnings = [line for line in captured.err.splitlines() if "session register" in line.lower()]
    assert len(warnings) == 1
    assert "exit 3" in warnings[0] and "register refused: no-engine" in warnings[0]


def test_given_no_register_command_when_agy_session_starts_then_nothing_runs(tmp_path, monkeypatch, capsys):
    log = _configure(tmp_path, None)

    assert _confirm_agy(tmp_path, monkeypatch) == 0

    assert capsys.readouterr().out.strip() == "{}"
    assert _calls(log) == []


def test_given_refused_confirmation_when_agy_session_starts_then_register_does_not_run(tmp_path, monkeypatch, capsys):
    log = _configure(tmp_path, ["register", "--client", "{client}"])

    assert _confirm_agy(tmp_path, monkeypatch, token_override="stale-token") == 1

    assert _calls(log) == []


def test_given_command_without_client_placeholder_when_agy_session_starts_then_warns_and_skips(
    tmp_path, monkeypatch, capsys
):
    log = _configure(tmp_path, ["register", "--client", "agy"])

    assert _confirm_agy(tmp_path, monkeypatch) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == "{}"
    assert _calls(log) == []
    assert "{client}" in captured.err


@pytest.mark.skipif(
    os.name != "posix",
    reason="OS exception: the Codex slot bridge needs Unix-domain sockets and a tmux pane",
)
def test_given_register_command_when_codex_session_start_confirms_then_it_runs_inside_the_client_tree(
    tmp_path,
    slot,  # noqa: F811
):
    process, reply, _record, _metadata = slot
    log = _configure(tmp_path, ["register", "--client", "{client}"])

    process.stdin.write("confirm\n")
    process.stdin.flush()

    assert reply() == "confirmed"
    (call,) = _calls(log)
    assert call["argv"] == ["register", "--client", "codex"]
    # The fake client is the engine; a register program finds it among its ancestors.
    assert process.pid in call["ancestors"]
    assert psutil.Process(process.pid).is_running()
