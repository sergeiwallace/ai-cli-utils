"""Finding a session by its name, and the commands' names and help (T-3.10, T-3.12).

The two fleet tools ``ai locate-session`` calls are separate programs an environment may
install. Here they are stood in for at the ``shutil.which`` and ``subprocess.run``
boundary, which records the exact command line each was given.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from name_fleet import HOST, OTHER_HOST, REPO, STEM, build_fleet

from ai_cli import name_registry as reg
from ai_cli import session_locate
from ai_cli.main import _cli_group

ADDED = [
    "list-names",
    "locate-session",
    "add-name",
    "remove-name",
    "sync-names",
    "check-names",
    "show-name-config",
    "list-name-candidates",
    "reconcile-names",
    "release-name",
    "reassign-name",
    "probe-transcripts",
]
VERBS = {"list", "locate", "add", "remove", "sync", "check", "show", "reconcile", "release", "reassign", "probe"}


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    fleet = build_fleet(tmp_path, monkeypatch)
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    monkeypatch.setattr("ai_cli.session_naming.resolve_host_alias", lambda: "box")
    return fleet


def _hold(fleet, name: str, machine: str) -> dict:
    registry = fleet.registry if machine == HOST else fleet.other_machine(f"m-{name}")
    slot = reg.Slot(repo=REPO, repo_alias=REPO, host_alias="box", label=None, machine=machine, client="claude-code")
    return reg.reserve_hand_chosen(
        registry, name, slot, add=False, via="add-name", added_by="t@box", words=frozenset()
    ).row


def _liveness_report(status: str, rung: str = "heartbeat", identity: str = f"{STEM}-cleo@{OTHER_HOST}") -> dict:
    """``fleet-liveness status --json``'s report: one record per matched identity."""
    return {
        "filters": {"machine": None, "session": identity},
        "identities": [{"identity": identity, "rung": rung, "status": status, "liveness": "live", "age_s": 30}],
        "rung_reasons": {},
    }


@pytest.fixture
def tools(monkeypatch):
    """Both fleet tools installed, answering with fixed JSON; ``calls`` records each command line.

    A tool named in ``slow`` runs past the bound it was given, as on a loaded host.
    """
    answers = {
        "fleet-liveness": _liveness_report("live"),
        "message-transports": {"rung": "git-inbox", "send": "git-inbox send --to x"},
    }
    calls: list[list[str]] = []
    installed = set(answers)
    slow: set[str] = set()
    real_run = session_locate.subprocess.run

    def which(name):
        return f"/opt/tools/{name}" if name in installed else None

    def run(command, *args, **kwargs):
        tool = Path(command[0]).name
        if tool not in answers:
            return real_run(command, *args, **kwargs)
        calls.append(list(command))
        if tool in slow:
            raise session_locate.subprocess.TimeoutExpired(command, kwargs["timeout"])
        answer = answers[tool]
        return SimpleNamespace(
            returncode=0, stdout=answer if isinstance(answer, str) else json.dumps(answer), stderr=""
        )

    monkeypatch.setattr(session_locate.shutil, "which", which)
    monkeypatch.setattr(session_locate.subprocess, "run", run)
    return SimpleNamespace(answers=answers, calls=calls, installed=installed, slow=slow)


def locate(*args):
    return CliRunner().invoke(_cli_group, ["locate-session", *args])


def test_given_a_name_held_and_running_here_when_located_then_it_is_active_with_its_worktree_and_rung(
    fleet, tools, monkeypatch
):
    _hold(fleet, "cleo", HOST)
    monkeypatch.setattr("ai_cli.session._tmux_session_names", lambda: [f"c-{STEM}-cleo"])

    result = locate("cleo")

    assert result.exit_code == 0, result.output
    assert f"{STEM}-cleo on {HOST}" in result.output
    assert "state      active" in result.output
    assert f"{REPO}/.worktrees/{STEM}-cleo" in result.output.replace("\\", "/")
    assert "reach      git-inbox" in result.output
    assert ["/opt/tools/message-transports", "resolve", "--target", f"{STEM}-cleo@{HOST}", "--json"] in tools.calls


def test_given_a_name_held_on_another_host_when_located_then_both_tools_run_and_their_words_are_printed(fleet, tools):
    _hold(fleet, "cleo", OTHER_HOST)

    result = locate("cleo")

    assert result.exit_code == 0, result.output
    target = f"{STEM}-cleo@{OTHER_HOST}"
    assert ["/opt/tools/fleet-liveness", "status", "--session", target, "--json"] in tools.calls
    assert ["/opt/tools/message-transports", "resolve", "--target", target, "--json"] in tools.calls
    assert "liveness   live (via heartbeat)" in result.output


def test_given_a_live_record_when_located_then_its_word_and_rung_are_printed_not_the_raw_json(fleet, tools):
    _hold(fleet, "cleo", OTHER_HOST)
    tools.answers["fleet-liveness"] = _liveness_report("live", rung="git-inbox")

    result = locate("cleo")

    assert result.exit_code == 0, result.output
    assert "liveness   live (via git-inbox)" in result.output
    assert '"identities"' not in result.output
    assert "not known to be active" not in result.output


@pytest.mark.parametrize("word", ["stale(3h)", "gone", "undetermined", "live (machine)"])
def test_given_a_liveness_word_other_than_live_when_located_then_it_is_printed_verbatim_with_its_rung(
    fleet, tools, word
):
    _hold(fleet, "cleo", OTHER_HOST)
    tools.answers["fleet-liveness"] = _liveness_report(word)

    result = locate("cleo")

    assert f"liveness   {word} (via heartbeat)" in result.output


def test_given_records_for_other_identities_when_located_then_the_located_sessions_record_is_read(fleet, tools):
    _hold(fleet, "cleo", OTHER_HOST)
    report = _liveness_report("gone", rung="buzz", identity=f"other@{OTHER_HOST}")
    report["identities"] += _liveness_report("live", rung="git-inbox")["identities"]
    tools.answers["fleet-liveness"] = report

    result = locate("cleo")

    assert "liveness   live (via git-inbox)" in result.output


def test_given_no_heartbeat_record_when_located_then_liveness_is_unknown_and_says_why(fleet, tools):
    _hold(fleet, "cleo", OTHER_HOST)
    tools.answers["fleet-liveness"] = {"filters": {}, "identities": [], "rung_reasons": {}}

    result = locate("cleo")

    assert f"liveness   unknown: fleet-liveness has no heartbeat record for {STEM}-cleo@{OTHER_HOST}" in result.output


@pytest.mark.parametrize(
    "answer", [{"identities": [{"identity": "x", "rung": "buzz"}]}, {"verdict": "fine"}, '["live"]', "not json"]
)
def test_given_a_record_without_a_liveness_word_when_located_then_it_says_the_record_is_unreadable(
    fleet, tools, answer
):
    _hold(fleet, "cleo", OTHER_HOST)
    tools.answers["fleet-liveness"] = answer

    result = locate("cleo")

    line = next(line for line in result.output.splitlines() if "liveness   " in line)
    assert "liveness   unknown: " in line
    assert "active" not in line


def test_given_fleet_liveness_runs_past_its_bound_when_located_then_it_reports_a_timeout_not_an_answer(fleet, tools):
    _hold(fleet, "cleo", OTHER_HOST)
    tools.slow.add("fleet-liveness")

    result = locate("cleo")

    assert result.exit_code == 0, result.output
    bound = session_locate.TOOL_TIMEOUT_S
    assert f"liveness   unknown: fleet-liveness timed out after {bound} s" in result.output
    assert "not known to be active" not in result.output
    assert "reach      git-inbox" in result.output


@pytest.mark.parametrize("missing", ["fleet-liveness", "message-transports"])
def test_given_a_fleet_tool_not_installed_when_located_then_the_row_is_printed_and_the_tool_named(
    fleet, tools, missing
):
    _hold(fleet, "cleo", OTHER_HOST)
    tools.installed.discard(missing)

    result = locate("cleo")

    assert result.exit_code == 0
    assert f"{STEM}-cleo on {OTHER_HOST}" in result.output
    assert f"{missing} is not installed" in result.output


def test_given_no_row_for_the_name_when_located_then_it_exits_one_saying_so(fleet, tools):
    result = locate("cleo")

    assert result.exit_code == 1
    assert "no session holds cleo" in result.output


def test_given_json_when_located_then_one_object_carries_all_four_readings(fleet, tools):
    _hold(fleet, "cleo", OTHER_HOST)

    result = locate("cleo", "--json")

    payload = json.loads(result.output.strip().splitlines()[-1])
    assert set(payload) == {"row", "local", "liveness", "reach"}
    assert payload["row"]["title"] == f"{STEM}-cleo"
    assert payload["local"] is None
    assert payload["liveness"] == _liveness_report("live")


def test_given_json_for_a_local_session_when_located_then_liveness_is_null(fleet, tools):
    _hold(fleet, "cleo", HOST)

    payload = json.loads(locate("cleo", "-j").output.strip().splitlines()[-1])

    assert payload["liveness"] is None
    assert payload["local"]["state"] in ("inactive", "gone")


def test_given_a_full_title_when_located_then_it_finds_the_same_row(fleet, tools):
    _hold(fleet, "cleo", HOST)

    assert "cleo:" in locate(f"{STEM}-cleo").output


# --- list-names and list-name-candidates ---------------------------------------------------------


def test_given_held_rows_when_listed_then_each_shows_its_title_host_client_and_kind(fleet):
    _hold(fleet, "cleo", HOST)
    _hold(fleet, "dora", OTHER_HOST)

    result = CliRunner().invoke(_cli_group, ["list-names"])
    rows = json.loads(CliRunner().invoke(_cli_group, ["list-names", "-j"]).output.strip().splitlines()[-1])

    assert result.exit_code == 0, result.output
    assert f"{STEM}-cleo" in result.output and OTHER_HOST in result.output
    by_token = {row["token"]: row for row in rows}
    assert by_token["cleo"]["running"] is False
    assert by_token["dora"]["running"] is None


def test_given_free_names_when_candidates_are_listed_then_each_length_and_the_ladder_are_counted(fleet, monkeypatch):
    monkeypatch.setattr("ai_cli.session.get_project_prefix", lambda: REPO)

    result = CliRunner().invoke(_cli_group, ["list-name-candidates", "-L", "3"])

    assert result.exit_code == 0, result.output
    assert re.search(r"3 letters: \d+ free", result.output)
    assert "4 letters" not in result.output


# --- T-3.12: names and help ---------------------------------------------------------------------


def test_given_the_cli_when_listed_then_every_command_this_design_adds_is_verb_noun():
    for name in ADDED:
        assert name in _cli_group.commands, f"{name} is not an ai command"
        verb, *nouns = name.split("-")
        assert verb in VERBS and nouns, f"{name} is not {{verb}}-{{noun}}"
        assert _cli_group.commands[name].help.startswith("Session names:")
    assert "session-names" not in _cli_group.commands


def test_given_ai_c_help_when_printed_then_the_three_naming_flags_have_one_line_each():
    lines = CliRunner().invoke(_cli_group, ["c", "--help"], terminal_width=80).output.splitlines()

    for flag in ("-N, --name", "-w, --with-name", "-u, --no-name"):
        [index] = [i for i, line in enumerate(lines) if flag in line]
        assert lines[index + 1].strip().startswith("-"), f"{flag}'s help runs onto a second line"


def test_given_the_readme_when_read_then_session_names_shows_the_form_flags_keys_prompt_and_commands():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("##### Human session names") :]
    section = section[: section.index("\n#### ")]

    assert "{repo-alias}-{host-alias}-[{label}-]{name}" in section
    for flag in ("-N/--name", "-w/--with-name", "-u/--no-name"):
        assert flag in section
    for key in ("enabled", "name_with_label", "min_name_length", "max_name_length"):
        assert key in section
    assert "Add it? [y/n]" in section and 'or "auto" to pick one' in section
    for name in ADDED:
        assert f"ai {name}" in section, f"the README does not list ai {name}"
