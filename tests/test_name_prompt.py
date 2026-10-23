"""The hand-chosen name flow, transition by transition (AC-SM-1).

Each test drives ``name_prompt.choose_by_hand`` against a real bare remote with a
scripted reader, and asserts the printed text, the exit code, the registry tip and the
spool. The pseudo-terminal tests at the end run the same flow in a child process whose
standard input is a real terminal, answered through the terminal's master side.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import textwrap
import time

import pytest
from name_fleet import HOST, OTHER_HOST, REPO, build_fleet

from ai_cli import name_prompt
from ai_cli import name_registry as reg

WORDS = frozenset({"may", "sun"})
SLOT = reg.Slot(repo=REPO, repo_alias=REPO, host_alias="box", label=None, machine=HOST, client="claude-code")
OTHER_SLOT = reg.Slot(repo=REPO, repo_alias=REPO, host_alias="two", label=None, machine=OTHER_HOST, client="codex")
QUESTION = name_prompt.QUESTION
LIMITS = "the configured limit is 3 to 4 letters (config/session-names.toml [limits])"


class Reader:
    """Answers the flow's questions from a script; ``None`` is end of input, ``KeyboardInterrupt`` is Ctrl-C."""

    def __init__(self, *answers, terminal: bool = True, on_answer=None):
        self.answers = list(answers)
        self.terminal = terminal
        self.asked: list[str] = []
        self.said: list[str] = []
        self.on_answer = on_answer or {}

    def ask(self, prompt: str) -> str | None:
        self.asked.append(prompt)
        if not self.answers:
            raise AssertionError(f"the flow asked {prompt!r} with no answer scripted")
        answer = self.answers.pop(0)
        if answer is KeyboardInterrupt:
            raise KeyboardInterrupt
        if answer in self.on_answer:
            self.on_answer[answer]()
        return answer

    @property
    def io(self) -> name_prompt.PromptIO:
        return name_prompt.PromptIO(terminal=self.terminal, ask=self.ask, say=self.said.append)


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    return build_fleet(tmp_path, monkeypatch)


def run(fleet, value: str, reader: Reader) -> reg.Reservation:
    return name_prompt.choose_by_hand(
        fleet.registry, SLOT, value, io=reader.io, words=WORDS, added_by="t@box", rng=random.Random(0)
    )


def exits(fleet, value: str, reader: Reader) -> name_prompt.PromptExit:
    with pytest.raises(name_prompt.PromptExit) as caught:
        run(fleet, value, reader)
    return caught.value


def hold(fleet, name: str) -> None:
    reg.reserve_hand_chosen(
        fleet.other_machine(f"other-{name}"), name, OTHER_SLOT, add=True, via="add-name", added_by="o@two", words=WORDS
    )


def add_manual(fleet, name: str) -> None:
    record = {"schema": 1, "name": name, "added_by": "t@box", "added_at": "x", "via": "add-name", "reason": None}
    fleet.registry.transact(lambda s: reg.Change({reg.manual_path(name): reg.encode_json(record)}, "add (fixture)"))


def test_t1_given_a_held_name_with_a_terminal_when_started_then_the_holder_is_named_and_the_question_asked(fleet):
    hold(fleet, "cleo")
    reader = Reader(None)

    outcome = exits(fleet, "cleo", reader)

    assert f"cleo is held by {REPO}-two-cleo@{OTHER_HOST}" in reader.said[0]
    assert reader.asked == [QUESTION]
    assert outcome.code == 130


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("cleo", f"held by {REPO}-two-cleo@{OTHER_HOST}"),
        ("clea", "one edit from the held name cleo"),
        ("sun", "keep_names"),
    ],
)
def test_t2_given_a_held_or_confusable_name_without_a_terminal_when_started_then_it_exits_two(fleet, value, reason):
    hold(fleet, "cleo")
    before = fleet.tip()

    outcome = exits(fleet, value, Reader(terminal=False))

    assert outcome.code == 2
    assert reason in outcome.message
    assert fleet.tip() == before


def test_t3_t18_given_a_known_free_in_limit_name_when_started_then_it_is_reserved_without_a_question(fleet):
    reader = Reader()

    reservation = run(fleet, "cleo", reader)

    assert reader.asked == []
    assert reservation.title == f"{REPO}-box-cleo"
    assert fleet.changed() == {"names/cleo.json"}


def test_t4_given_a_known_name_outside_the_limits_with_a_terminal_when_started_then_the_limits_are_shown(fleet):
    add_manual(fleet, "rebecca")
    reader = Reader(None)

    exits(fleet, "rebecca", reader)

    assert reader.said[0] == f'"rebecca" is 7 letters; {LIMITS}.'
    assert reader.asked == [QUESTION]


def test_t5_given_a_known_name_outside_the_limits_without_a_terminal_when_started_then_it_exits_two(fleet):
    add_manual(fleet, "rebecca")
    before = fleet.tip()

    outcome = exits(fleet, "rebecca", Reader(terminal=False))

    assert outcome.code == 2
    assert LIMITS in outcome.message
    assert fleet.tip() == before


def test_t6_given_an_unknown_name_with_a_terminal_when_started_then_it_asks_to_add_it(fleet):
    reader = Reader(None)

    exits(fleet, "zara", reader)

    assert reader.asked == ["zara is not in the name database. Add it? [y/n]"]


def test_t7_given_an_unknown_name_without_a_terminal_when_started_then_it_exits_two_naming_add_name(fleet):
    before = fleet.tip()

    outcome = exits(fleet, "zara", Reader(terminal=False))

    assert outcome.code == 2
    assert "ai add-name zara" in outcome.message
    assert fleet.tip() == before


@pytest.mark.parametrize("yes", ["y", "YES", "Yes"])
def test_t8_given_yes_for_an_in_limit_name_when_answered_then_one_commit_adds_and_holds_it(fleet, yes):
    reservation = run(fleet, "zara", Reader(yes))

    assert reservation.added_to_database
    assert fleet.changed() == {"available/manual/zara.json", "names/zara.json"}


def test_t9_given_yes_for_a_name_outside_the_limits_when_answered_then_the_limits_are_shown_and_the_question_asked(
    fleet,
):
    reader = Reader("y", None)

    exits(fleet, "rebecca", reader)

    assert reader.said == [f'"rebecca" is 7 letters; {LIMITS}.']
    assert reader.asked[-1] == QUESTION


@pytest.mark.parametrize("no", ["n", "NO"])
def test_t10_given_no_when_answered_then_the_free_text_question_is_asked(fleet, no):
    reader = Reader(no, None)

    exits(fleet, "zara", reader)

    assert reader.asked == ["zara is not in the name database. Add it? [y/n]", QUESTION]


def test_t11_given_another_answer_to_the_y_n_question_when_answered_then_it_asks_again(fleet):
    reader = Reader("maybe", "", None)

    exits(fleet, "zara", reader)

    assert reader.asked == ["zara is not in the name database. Add it? [y/n]"] * 3


def test_t12_given_auto_at_the_free_text_question_when_answered_then_a_name_is_picked(fleet):
    hold(fleet, "cleo")

    reservation = run(fleet, "cleo", Reader("auto"))

    assert reservation.row["source"] == "picked"
    assert reservation.name != "cleo"


def test_t13_given_a_held_value_at_the_free_text_question_when_answered_then_it_asks_again(fleet):
    hold(fleet, "cleo")
    reader = Reader("n", "cleo", None)

    exits(fleet, "zara", reader)

    assert "held by" in reader.said[0]
    assert reader.asked[-2:] == [QUESTION, QUESTION]


def test_t14_given_a_value_over_the_limit_at_the_free_text_question_when_answered_then_its_length_is_shown(fleet):
    reader = Reader("n", "rebecca", None)

    exits(fleet, "zara", reader)

    assert reader.said == [f'"rebecca" is 7 letters; {LIMITS}.']
    assert reader.asked[-1] == QUESTION


@pytest.mark.parametrize("value", ["al", "jo3", "ann-e"])
def test_t15_given_a_short_or_non_letter_value_at_the_free_text_question_when_answered_then_the_limits_are_shown(
    fleet, value
):
    reader = Reader("n", value, None)

    exits(fleet, "zara", reader)

    assert LIMITS in reader.said[0]
    assert reader.asked[-1] == QUESTION


def test_t16_given_an_unknown_in_limit_value_at_the_free_text_question_when_answered_then_it_is_added_without_asking(
    fleet,
):
    reader = Reader("n", "zoe")

    reservation = run(fleet, "zara", reader)

    assert reservation.name == "zoe"
    assert reader.asked == ["zara is not in the name database. Add it? [y/n]", QUESTION]
    assert fleet.changed() == {"available/manual/zoe.json", "names/zoe.json"}


@pytest.mark.parametrize("answers", [(None,), ("n", None), ("n", KeyboardInterrupt), (KeyboardInterrupt,)])
def test_t17_given_end_of_input_or_ctrl_c_at_a_question_when_answered_then_it_exits_130_writing_nothing(fleet, answers):
    before = fleet.tip()

    outcome = exits(fleet, "zara", Reader(*answers))

    assert outcome.code == 130
    assert fleet.tip() == before
    assert reg.read_spool() == {}


def _land_first(monkeypatch, fleet, conflicting):
    real_push = reg.GitRegistry._push
    pushes: list[bool] = []

    def push(self, commit):
        if self is fleet.registry and not pushes:
            conflicting()
        result = real_push(self, commit)
        if self is fleet.registry:
            pushes.append(result)
        return result

    monkeypatch.setattr(reg.GitRegistry, "_push", push)
    return pushes


def test_t19_given_the_name_taken_before_the_push_with_a_terminal_when_written_then_the_new_reason_is_shown(
    fleet, monkeypatch
):
    _land_first(monkeypatch, fleet, lambda: hold(fleet, "zara"))
    reader = Reader("y", None)

    exits(fleet, "zara", reader)

    assert "zara is held by" in reader.said[0]
    assert reader.asked[-1] == QUESTION


def test_t20_given_the_name_taken_before_the_push_without_a_terminal_when_written_then_it_exits_two(fleet, monkeypatch):
    _land_first(monkeypatch, fleet, lambda: hold(fleet, "cleo"))

    outcome = exits(fleet, "cleo", Reader(terminal=False))

    assert outcome.code == 2
    assert "cleo is held by" in outcome.message


def test_t21_given_the_picked_name_taken_before_the_push_when_written_then_it_picks_again(fleet, monkeypatch):
    hold(fleet, "cleo")
    taken: list[str] = []

    def take_the_pick():
        snapshot = fleet.registry.snapshot()
        request, _ = reg.pick_request(fleet.registry, snapshot, SLOT)
        from ai_cli import session_naming

        name = session_naming.pick_name(request, random.Random(0)).name
        taken.append(name)
        hold(fleet, name)

    _land_first(monkeypatch, fleet, take_the_pick)

    reservation = run(fleet, "cleo", Reader("auto"))

    assert reservation.name != taken[0]
    assert fleet.rows()[taken[0]]["machine"] == OTHER_HOST


def test_t22_given_no_free_candidate_after_auto_when_written_then_it_exits_non_zero_naming_the_filters(
    tmp_path, monkeypatch
):
    fleet = build_fleet(tmp_path, monkeypatch, names=["cleo", "dora"])
    hold(fleet, "cleo")
    hold(fleet, "dora")

    outcome = exits(fleet, "cleo", Reader("auto"))

    assert outcome.code == 1
    assert "held: 2" in outcome.message


def test_t23_given_the_registry_lost_before_a_hand_chosen_push_when_written_then_it_exits_75_taking_no_spare(fleet):
    reg.reserve_picked(fleet.registry, SLOT, words=WORDS)  # tops up this machine's spare
    reader = Reader("y", on_answer={"y": fleet.offline})

    outcome = exits(fleet, "zara", reader)

    assert outcome.code == 75
    assert "could not be written" in outcome.message
    assert reg.read_spool() == {}


def test_t24_given_the_registry_lost_after_auto_with_a_spare_when_written_then_the_spare_is_taken(fleet):
    reg.reserve_picked(fleet.registry, SLOT, words=WORDS)
    hold(fleet, "cleo")
    [spare] = [row["token"] for row in fleet.rows().values() if row["kind"] == "spare" and row["machine"] == HOST]
    reader = Reader("auto", on_answer={"auto": fleet.offline})

    reservation = run(fleet, "cleo", reader)

    assert reservation.offline and reservation.name == spare
    assert reader.said[-1] == f"offline: using reserved spare {spare}"
    assert set(reg.read_spool()) == {spare}


def test_t25_given_the_registry_lost_after_auto_and_no_spare_when_written_then_it_exits_75_naming_reconcile(fleet):
    hold(fleet, "cleo")
    reader = Reader("auto", on_answer={"auto": fleet.offline})

    outcome = exits(fleet, "cleo", reader)

    assert outcome.code == 75
    assert "ai reconcile-names" in outcome.message
    assert reg.read_spool() == {}


def test_t26_given_a_terminal_state_when_reached_then_nothing_more_is_read_or_written(fleet):
    reader = Reader("y", "never read")

    run(fleet, "zara", reader)
    tip = fleet.tip()

    assert reader.answers == ["never read"]
    assert fleet.tip() == tip


def test_given_the_registry_unreachable_at_the_start_when_a_name_is_chosen_then_it_exits_75_taking_no_spare(fleet):
    reg.reserve_picked(fleet.registry, SLOT, words=WORDS)
    fleet.offline()

    outcome = exits(fleet, "zara", Reader())

    assert outcome.code == 75
    assert "cannot be proved free" in outcome.message
    assert reg.read_spool() == {}


# --- through a pseudo-terminal --------------------------------------------------------

_CHILD = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path
    from ai_cli import config, main, name_prompt
    from ai_cli import name_registry as reg
    repo = Path(sys.argv[1])
    registry = reg.GitRegistry(repo, ref=sys.argv[2], remote="origin", policy_file=repo / reg.POLICY_MARKER,
                               timeout_s=15, attempts=5)
    slot = reg.Slot(repo="myrepo", repo_alias="myrepo", host_alias="box", label=None, machine="box-one",
                    client="claude-code")
    io = name_prompt.PromptIO(terminal=config._can_prompt(), ask=main._prompt_line,
                              say=lambda message: sys.stderr.write(message + "\\n"))
    try:
        reservation = name_prompt.choose_by_hand(registry, slot, sys.argv[3], io=io, words=frozenset(),
                                                 added_by="t@box")
    except name_prompt.PromptExit as exc:
        print("RESULT " + json.dumps({"code": exc.code}), flush=True)
    else:
        print("RESULT " + json.dumps({"name": reservation.name}), flush=True)
    """
)


def _converse(fleet, value: str, exchanges: list[tuple[str, str | None]]) -> tuple[dict, str]:
    """Run the child on a pseudo-terminal; for each (expected prompt, reply) wait for the prompt, then reply."""
    import pty
    import select

    master, slave = pty.openpty()
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(fleet.repo), "refs/heads/fleet-session-names/registry", value],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=dict(os.environ),
        close_fds=True,
    )
    os.close(slave)
    seen = ""
    transcript: list[str] = []

    def read_until(marker: str, deadline: float) -> None:
        nonlocal seen
        while marker not in seen:
            if time.monotonic() > deadline:
                raise AssertionError(f"waited for {marker!r}; the terminal showed:\n{seen}")
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    seen += os.read(master, 4096).decode(errors="replace")
                except OSError:
                    break

    try:
        for prompt, reply in exchanges:
            read_until(prompt, time.monotonic() + 60)
            transcript.append(seen)
            seen = seen[seen.index(prompt) + len(prompt) :]
            # Ctrl-D on an empty line is end of input on a terminal.
            os.write(master, b"\x04" if reply is None else f"{reply}\n".encode())
        read_until("RESULT ", time.monotonic() + 60)
        read_until("}", time.monotonic() + 10)
        child.wait(timeout=30)
    finally:
        os.close(master)
        if child.poll() is None:
            child.kill()
    line = seen[seen.index("RESULT ") + len("RESULT ") :].splitlines()[0]
    return json.loads(line), "".join(transcript) + seen


pty_only = pytest.mark.skipif(
    sys.platform == "win32", reason="pseudo-terminals are POSIX; the scripted tests cover Windows"
)


@pty_only
def test_given_a_terminal_when_an_unknown_name_is_added_then_the_y_n_question_reaches_the_terminal(fleet):
    result, _ = _converse(fleet, "zara", [("Add it? [y/n]", "y")])

    assert result == {"name": "zara"}
    assert fleet.changed() == {"available/manual/zara.json", "names/zara.json"}


@pty_only
def test_given_a_terminal_when_no_then_a_value_over_the_limit_then_a_valid_one_then_each_message_is_shown(fleet):
    result, seen = _converse(fleet, "zara", [("Add it? [y/n]", "n"), (QUESTION, "rebecca"), ("3 to 4 letters", "zoe")])

    assert result == {"name": "zoe"}
    assert '"rebecca" is 7 letters' in seen


@pty_only
def test_given_a_terminal_when_input_ends_at_the_question_then_it_exits_130_writing_nothing(fleet):
    before = fleet.tip()

    result, _ = _converse(fleet, "zara", [("Add it? [y/n]", None)])

    assert result == {"code": 130}
    assert fleet.tip() == before
