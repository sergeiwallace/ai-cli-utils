"""`ai agy` launches an interactive Antigravity session on its Google sign-in, never an API key."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_cli.main import _bare_engine_command, _do_session_launch
from ai_cli.session_script import get_engine_script

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the session script runs under bash")

NAME = "myproject-1"
UNSET = ["-u", "GEMINI_API_KEY", "-u", "GOOGLE_API_KEY", "-u", "GOOGLE_GENAI_USE_VERTEXAI"]


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def _sign_in(home: Path) -> None:
    (home / ".gemini").mkdir(parents=True, exist_ok=True)
    (home / ".gemini" / "oauth_creds.json").write_text("{}", encoding="utf-8")


def _settings(home: Path, **values: str) -> None:
    path = home / ".gemini" / "antigravity-cli" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values), encoding="utf-8")


def _launch(which=lambda name: f"/usr/bin/{name}"):
    with (
        patch("ai_cli.main.shutil.which", side_effect=which),
        patch("ai_cli.session._resolve_is_remote", return_value=False),
        pytest.raises(SystemExit) as exc_info,
    ):
        _do_session_launch(
            engine="agy",
            name="1",
            resume=False,
            once=False,
            bare=True,
            notify=False,
            sandbox=False,
            no_worktree=True,
            remote=False,
            project="",
            is_remote=False,
            project_prefix_override="myproject",
            extra_args=[],
            config={},
        )
    return exc_info.value.code


def test_given_no_google_sign_in_when_launching_ai_agy_then_it_refuses_and_names_the_sign_in_step(home, capsys):
    code = _launch()

    assert code == 1
    err = capsys.readouterr().err
    assert "no Google sign-in found for agy" in err
    assert "finish the Google sign-in" in err


@pytest.mark.parametrize("provider", ["gemini", "vertex"])
def test_given_agy_set_to_an_api_provider_when_launching_then_it_refuses_rather_than_bill_the_api(
    home, capsys, provider
):
    _sign_in(home)
    _settings(home, modelProvider=provider)

    code = _launch()

    assert code == 1
    assert f'"modelProvider": "{provider}"' in capsys.readouterr().err


def test_given_agy_not_installed_when_launching_then_it_refuses_with_the_install_step(home, capsys):
    _sign_in(home)

    code = _launch(which=lambda name: None if name == "agy" else f"/usr/bin/{name}")

    assert code == 1
    assert "agy executable not found" in capsys.readouterr().err


def test_given_a_signed_in_account_and_an_exported_api_key_when_launching_then_agy_starts_without_the_key(
    home, monkeypatch
):
    _sign_in(home)
    _settings(home, colorScheme="dark")
    monkeypatch.setenv("GEMINI_API_KEY", "placeholder-not-a-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "placeholder-not-a-key")
    launched: dict = {}

    def exec_with_direnv(root, command, **_kwargs):
        launched["command"] = command
        launched["env"] = {name for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY") if name in os.environ}
        raise SystemExit(0)

    with patch("ai_cli.main._exec_with_direnv", side_effect=exec_with_direnv):
        code = _launch()

    assert code == 0
    assert launched["env"] == set()
    assert "agy" in launched["command"]


def test_given_an_agy_launch_when_the_bare_command_is_built_then_it_is_a_fresh_interactive_agy(tmp_path):
    command = _bare_engine_command("agy", NAME, tmp_path, None, "gemini", "--no-sandbox", ["--extra"])

    if sys.platform == "win32":
        assert command == ["agy", "--extra"]
    else:
        assert command == ["env", *UNSET, "agy", "--extra"]


def _run_dispatch(tmp_path: Path, *, prompt: str = "") -> list[str]:
    script = get_engine_script(
        engine="agy", ai_name=NAME, session=f"agy-{NAME}", prefix="agy-myproject-", project_prefix="myproject"
    )
    start = script.index("_ai_run_agy() {")
    end = script.index("# END BACKGROUND ATTACH", script.index("# BEGIN BACKGROUND ATTACH prompted-end"))
    log = tmp_path / "calls.log"
    prompt_file = tmp_path / "resume-prompt"
    if prompt:
        prompt_file.write_text(prompt, encoding="utf-8")
    harness = tmp_path / "dispatch.sh"
    harness.write_text(
        f"""
engine=agy
ai_name={NAME}
tmux_session=agy-{NAME}
TMUX_PANE=%42
first_run=true
prompt_file={prompt_file}
_ai_launch_log=""
resolve_status=1
matched_file=""
bg_target=""
claude_perms_flag=""
run_agent() {{ printf 'RUN %s\\n' "$*" >> {log}; }}
ai() {{ printf 'AI %s\\n' "$*" >> {log}; return 0; }}
tmux() {{ printf 'TMUX %s\\n' "$*" >> {log}; }}
sleep() {{ :; }}
{script[start:end]}
wait
""",
        encoding="utf-8",
    )
    done = subprocess.run(["bash", str(harness)], capture_output=True, text=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


@posix_only
def test_given_an_agy_session_when_the_session_script_launches_it_then_agy_runs_without_the_api_keys(tmp_path):
    calls = _run_dispatch(tmp_path)

    assert f"RUN env {' '.join(UNSET)} agy" in calls
    assert not any("codex" in call for call in calls)


@posix_only
def test_given_a_resume_prompt_when_the_session_script_relaunches_agy_then_the_prompt_is_typed_into_its_pane(
    tmp_path,
):
    calls = _run_dispatch(tmp_path, prompt="carry on")

    assert f"RUN env {' '.join(UNSET)} agy" in calls
    assert f"TMUX send-keys -t agy-{NAME} carry on C-m" in calls


@posix_only
def test_given_an_agy_session_when_its_script_starts_then_it_exports_the_names_fleet_messaging_reads():
    script = get_engine_script(
        engine="agy", ai_name=NAME, session=f"agy-{NAME}", prefix="agy-myproject-", project_prefix="myproject"
    )

    assert 'if [[ "$engine" == "agy" ]]; then export CLAUDE_CODE_TASK_LIST_ID="$ai_name"' in script
    assert 'export AI_SESSION_NAME="$ai_name"' in script
