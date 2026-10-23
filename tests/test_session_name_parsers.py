"""The sites that read a session's title, given human-named and index-named titles (T-3.6).

Each site is checked both ways: a human-named title is a managed session with its stem,
label and name, and an index-named title behaves as it always did.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from name_fleet import STEM
from test_iterm2 import make_iterm2_config

from ai_cli import chief_of_staff, session, session_adopt, session_naming, session_registry, session_title
from ai_cli.iterm2 import _assign_iterm2_color_slot, _iterm2_palette
from ai_cli.session_script import get_engine_script

NAMES = frozenset({"ada", "cleo", "iris"})


@pytest.fixture
def names(monkeypatch):
    """The session-name set every site reads, fixed for the test."""
    monkeypatch.setattr("ai_cli.name_registry.known_names", lambda registry=None: NAMES)
    return NAMES


# --- the explicit-slot rule and the title parser -----------------------------------------------


@pytest.mark.parametrize(
    ("clean", "expected"),
    [
        ("ada", session_title.Positional("title", "ada", "ada")),
        ("review-ada", session_title.Positional("title", "review-ada", "ada")),
        ("3", session_title.Positional("slot", "3")),
        ("review-3", session_title.Positional("slot", "review-3")),
        ("review", session_title.Positional("label", "review")),
        ("", session_title.Positional("empty", "")),
    ],
)
def test_given_a_positional_when_classified_then_names_reattach_and_indexes_keep_their_slot_meaning(clean, expected):
    assert session_title.classify_positional(clean, NAMES) == expected


@pytest.mark.parametrize(
    ("title", "label", "name", "index"),
    [
        (f"{STEM}-ada", None, "ada", None),
        (f"{STEM}-review-ada", "review", "ada", None),
        (f"{STEM}-3", None, None, 3),
        (f"{STEM}-review-3", "review", None, 3),
    ],
)
def test_given_a_title_when_parsed_then_its_stem_label_and_name_or_index_are_split(title, label, name, index):
    assert session_title.parse_title(title, STEM, NAMES) == session_title.ParsedTitle(STEM, label, name, index)


# --- the liveness pattern and the resume fallback --------------------------------------------------


@pytest.mark.parametrize(
    "name", ["c-myrepo-box-ada", "cx-r-myrepo-box-review-ada", "c-myrepo-box-3", "g-r-myrepo-box-12"]
)
def test_given_an_engine_prefixed_session_name_when_matched_then_it_is_a_managed_session(name):
    assert session._AI_SESSION_RE.fullmatch(name)


@pytest.mark.parametrize("name", ["myrepo-box-ada", "myrepo-box-3"])
def test_given_a_bare_title_when_its_live_tmux_session_is_checked_then_the_engine_forms_are_tried(name):
    assert session._has_live_tmux_session(name, {f"c-{name}"})


@pytest.mark.parametrize("slot", ["ada", "3"])
def test_given_a_session_under_a_differently_cased_prefix_when_resumed_by_slot_then_it_is_found(monkeypatch, slot):
    """The fallback the resume path keeps for prefix casing reaches a name exactly as it reaches an index."""
    cased = f"c-MyRepo-box-{slot}"
    monkeypatch.setattr(session, "_tmux_session_names", lambda: [cased])
    monkeypatch.setattr(session.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""))

    assert session.resolve_session(f"c-{STEM}-", slot) == cased


# --- adopt, the iTerm2 registry and the chief of staff -----------------------------------------------


def test_given_a_human_named_title_when_split_by_adopt_then_its_prefix_and_name_come_back():
    assert session_adopt.split_human_name(f"{STEM}-ada", NAMES) == (STEM, "ada")
    assert session_adopt.split_human_name(f"{STEM}-review", NAMES) is None
    assert session_adopt.split_ai_name(f"{STEM}-3") == (STEM, 3)


@pytest.mark.parametrize(("value", "expected"), [("ada", True), ("review-ada", True), ("3", True), ("review", False)])
def test_given_a_slot_when_the_iterm2_registry_checks_it_then_names_and_indexes_both_address_one_session(
    names, value, expected
):
    assert session_registry._is_slot(value) is expected


@pytest.fixture
def chief(monkeypatch, names):
    monkeypatch.setattr(chief_of_staff, "_session_name", lambda: "chief")


@pytest.mark.parametrize(
    ("name", "expected"),
    [("chief", True), ("chief-1", True), ("chief-ada", True), (f"{STEM}-chief-ada", True), ("chief-review", False)],
)
def test_given_a_positional_when_checked_for_the_chief_then_a_name_tail_is_the_chief(chief, name, expected):
    assert chief_of_staff.is_chief_positional(name) is expected


@pytest.mark.parametrize(
    ("slot", "expected"), [("chief-ada", True), ("chief-2", True), ("chief", False), ("chief-review", False)]
)
def test_given_a_chief_slot_when_checked_then_a_name_or_an_index_names_one_chief(chief, slot, expected):
    assert chief_of_staff.is_numbered_slot(slot) is expected


@pytest.mark.parametrize(
    ("requested", "running", "expected"),
    [
        ("chief", "chief-ada", True),
        ("chief-ada", "chief-ada", True),
        ("chief-iris", "chief-ada", False),
        ("chief", "chief-1", True),
    ],
)
def test_given_a_running_chief_when_a_request_is_compared_then_an_unnamed_request_reaches_it(
    chief, requested, running, expected
):
    assert chief_of_staff.same_chief(requested, running) is expected


# --- the session number and the iTerm2 colour ----------------------------------------------------------


def _session_number(shell: str, ai_name: str) -> str:
    script = get_engine_script("c", ai_name, f"c-{ai_name}", "c-myproject-", "myproject")
    lines = script.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip().startswith("_session_num=$("))
    end = next(i for i, line in enumerate(lines) if line.strip() == '_session_num="${_session_num:-1}"')
    snippet = (
        f"ai_name={shlex.quote(ai_name)}\n" + "\n".join(lines[start : end + 1]) + '\nprintf "%s" "$_session_num"\n'
    )
    result = subprocess.run([shell, "-c", snippet], capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="the generated session script runs under a POSIX shell")
@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_given_two_human_named_sessions_when_the_script_numbers_them_then_they_differ_and_repeat_stably(shell):
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} not installed")

    ada, iris = _session_number(shell, f"{STEM}-ada"), _session_number(shell, f"{STEM}-iris")

    assert ada != iris
    assert ada == _session_number(shell, f"{STEM}-ada") == str(session_naming.name_number("ada"))
    assert _session_number(shell, f"{STEM}-3") == "3"


def test_given_two_human_named_sessions_when_colours_are_assigned_then_each_takes_its_names_slot_across_a_restart(
    tmp_path,
):
    cfg = make_iterm2_config(collision_avoidance=True)
    palette = [color for _name, color in _iterm2_palette(cfg)]
    expected = {name: palette[(session_naming.name_number(name) - 1) % len(palette)] for name in ("cleo", "bert")}
    assert expected["cleo"] not in (expected["bert"], palette[0])
    with (
        patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False),
        patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path),
        patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg),
    ):
        first = {name: _assign_iterm2_color_slot(f"{STEM}-{name}", "c") for name in ("cleo", "bert")}
        (tmp_path / "color-leases.json").unlink()
        after_restart = {name: _assign_iterm2_color_slot(f"{STEM}-{name}", "c") for name in ("bert", "cleo")}

    assert first == after_restart == expected


def test_given_index_named_sessions_when_colours_are_assigned_then_the_lowest_free_slot_is_used_as_before(tmp_path):
    cfg = make_iterm2_config(collision_avoidance=True)
    with (
        patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}, clear=False),
        patch("ai_cli.iterm2._iterm2_state_dir", return_value=tmp_path),
        patch("ai_cli.iterm2._load_iterm2_config", return_value=cfg),
    ):
        first = _assign_iterm2_color_slot(f"{STEM}-1", "c")
        second = _assign_iterm2_color_slot(f"{STEM}-2", "c")

    palette = [color for _name, color in _iterm2_palette(cfg)]
    assert [first, second] == palette[:2]


@pytest.mark.parametrize(
    ("argv", "name", "expected"),
    [
        (["c", "-N", "cleo", "probe"], "probe", ["ai", "c", f"{STEM}-probe-cleo"]),
        (["c", "--name", "cleo", "-w"], "", ["ai", "c", f"{STEM}-probe-cleo"]),
        (["c", "3"], "3", ["ai", "c", "3"]),
    ],
)
def test_given_a_launch_when_its_relaunch_command_is_recorded_then_a_human_title_replaces_the_naming_flags(
    monkeypatch, argv, name, expected
):
    from ai_cli import main

    monkeypatch.setattr(sys, "argv", ["ai", *argv])
    session_name = f"{STEM}-probe-cleo" if name != "3" else f"{STEM}-3"

    assert main._relaunch_argv(name, session_name) == expected
