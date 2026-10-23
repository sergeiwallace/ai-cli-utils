"""Title composition and the launch flags (T-3.5, AC-DT-1).

The rules run through ``_resolve_session_identity``, the naming step every launch takes
before it creates a tmux session or a worktree, against a real bare remote. Each test
asserts the title and the registry file it wrote, or that nothing was written.
"""

from __future__ import annotations

import itertools

import pytest
from name_fleet import HOST, HOST_ALIAS, OTHER_HOST, REPO, STEM, build_fleet, quiet_reporter

from ai_cli import main, session_title
from ai_cli import name_registry as reg

LIVE_TMUX: list[str] = []


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_CLI_SESSION_NAMES", "1")
    monkeypatch.delenv("AI_CLI_SESSION_NAME_WITH_LABEL", raising=False)
    LIVE_TMUX.clear()
    # No tmux server belongs to these tests; the live session list is what they stage.
    monkeypatch.setattr("ai_cli.session._tmux_session_names", lambda: list(LIVE_TMUX))
    return build_fleet(tmp_path, monkeypatch)


def resolve(name: str = "", *, dry_run: bool = False, engine: str = "c", **naming):
    reporter, stream = quiet_reporter()
    identity = main._resolve_session_identity(
        engine,
        STEM,
        name,
        {},
        is_remote=False,
        use_tmux=True,
        legacy_prefix=REPO,
        naming=main._NamingRequest(**naming),
        repo=REPO,
        repo_alias=REPO,
        host_alias=HOST_ALIAS,
        worktree=True,
        dry_run=dry_run,
        reporter=reporter,
    )
    return identity, stream.getvalue()


def refused(name: str = "", **naming) -> tuple[int, str]:
    reporter, stream = quiet_reporter()
    with pytest.raises(SystemExit) as caught:
        main._resolve_session_identity(
            "c",
            STEM,
            name,
            {},
            is_remote=False,
            use_tmux=True,
            legacy_prefix=REPO,
            naming=main._NamingRequest(**naming),
            repo=REPO,
            repo_alias=REPO,
            host_alias=HOST_ALIAS,
            worktree=True,
            dry_run=False,
            reporter=reporter,
        )
    return int(caught.value.code), stream.getvalue()


def new_rows(fleet, before: dict) -> dict:
    return {token: row for token, row in fleet.rows().items() if token not in before}


def session_rows(rows: dict) -> dict:
    return {token: row for token, row in rows.items() if row["kind"] != "spare"}


# --- AC-DT-1, rule by rule -------------------------------------------------------------


def test_r1_given_no_label_and_no_name_when_launched_then_the_title_ends_in_a_picked_name(fleet):
    before = fleet.rows()

    (session_id, title), _ = resolve()

    rows = session_rows(new_rows(fleet, before))
    [(token, row)] = rows.items()
    assert title == f"{STEM}-{token}"
    assert session_id == f"c-{title}"
    assert row["kind"] == "session" and row["source"] == "picked" and row["title"] == title


def test_r2_given_a_name_and_no_label_when_launched_then_the_title_ends_in_that_name(fleet):
    (_, title), _ = resolve(custom="cleo")

    assert title == f"{STEM}-cleo"
    assert fleet.rows()["cleo"]["title"] == title


def test_r2_given_with_name_and_a_name_when_launched_then_the_title_ends_in_that_name(fleet):
    (_, title), _ = resolve(custom="cleo", with_name=True)

    assert title == f"{STEM}-cleo"


def test_r3_given_no_name_without_a_label_when_launched_then_it_exits_two_writing_nothing(fleet):
    before = fleet.tip()

    code, output = refused(no_name=True)

    assert code == 2
    assert "--no-name needs a label" in output
    assert fleet.tip() == before


def test_r4_given_a_label_under_keep_when_launched_then_a_picked_name_follows_the_label(fleet):
    before = fleet.rows()

    (_, title), _ = resolve("review")

    [(token, row)] = session_rows(new_rows(fleet, before)).items()
    assert title == f"{STEM}-review-{token}"
    assert row["label"] == "review"


def test_r5_given_a_label_under_drop_when_launched_then_the_title_is_the_label_held_by_a_label_row(fleet, monkeypatch):
    monkeypatch.setenv("AI_CLI_SESSION_NAME_WITH_LABEL", "drop")

    (_, title), _ = resolve("probe")

    assert title == f"{STEM}-probe"
    row = fleet.rows()["probe"]
    assert row["kind"] == "label" and row["name"] is None and row["title"] == title


def test_r6_given_a_label_and_with_name_under_drop_when_launched_then_a_picked_name_follows(fleet, monkeypatch):
    monkeypatch.setenv("AI_CLI_SESSION_NAME_WITH_LABEL", "drop")
    before = fleet.rows()

    (_, title), _ = resolve("review", with_name=True)

    [(token, _row)] = session_rows(new_rows(fleet, before)).items()
    assert title == f"{STEM}-review-{token}"


def test_r7_given_a_label_and_no_name_when_launched_then_the_title_is_the_label(fleet):
    (_, title), _ = resolve("probe", no_name=True)

    assert title == f"{STEM}-probe"
    assert fleet.rows()["probe"]["kind"] == "label"


def test_r8_given_a_label_and_a_name_when_launched_then_the_name_follows_the_label(fleet):
    (_, title), _ = resolve("probe", custom="cleo")

    assert title == f"{STEM}-probe-cleo"
    assert fleet.rows()["cleo"]["label"] == "probe"


@pytest.mark.parametrize("label", ["", "probe"])
def test_r9_given_a_name_and_no_name_when_launched_then_it_exits_two_writing_nothing(fleet, label):
    before = fleet.tip()

    code, output = refused(label, custom="cleo", no_name=True)

    assert code == 2
    assert "--name conflicts with --no-name" in output
    assert fleet.tip() == before


def _table(label: bool, custom: bool, flag: str | None, policy: str) -> str:
    """AC-DT-1 written out as the design states it, rule by rule."""
    if custom and flag == "no":
        return "R9"
    if not label and not custom:
        return "R3" if flag == "no" else "R1"
    if not label and custom:
        return "R2"
    if label and not custom:
        if flag is None:
            return "R4" if policy == "keep" else "R5"
        return "R6" if flag == "with" else "R7"
    return "R8"


_RULE_KIND = {
    "R1": "picked",
    "R2": "custom",
    "R4": "picked",
    "R5": "label",
    "R6": "picked",
    "R7": "label",
    "R8": "custom",
}


@pytest.mark.parametrize(
    ("label", "custom", "flag", "policy"),
    list(itertools.product([False, True], [False, True], [None, "with", "no"], ["keep", "drop"])),
)
def test_given_every_feasible_combination_when_composed_then_exactly_one_rule_decides_it(label, custom, flag, policy):
    rule = _table(label, custom, flag, policy)
    arguments = {
        "label": "probe" if label else None,
        "custom": "cleo" if custom else None,
        "flag": flag,
        "policy": policy,
    }

    if rule in ("R3", "R9"):
        with pytest.raises(session_title.TitleError):
            session_title.compose_kind(**arguments)
    else:
        assert session_title.compose_kind(**arguments) == _RULE_KIND[rule]


# --- T-3.5 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "why"), [("review-3", "reads as a session index"), ("team-ada", "is a session name")]
)
def test_given_a_label_ending_in_a_name_or_digits_with_a_name_when_launched_then_it_exits_two(fleet, label, why):
    before = fleet.tip()

    code, output = refused(label, custom="cleo")

    assert code == 2
    assert why in output
    assert fleet.tip() == before


def test_given_a_held_name_when_chosen_then_it_exits_two_naming_the_holder_and_machine(fleet):
    other = fleet.other_machine()
    reg.reserve_hand_chosen(
        other,
        "cleo",
        reg.Slot(repo=REPO, repo_alias=REPO, host_alias="two", label=None, machine=OTHER_HOST, client="claude-code"),
        add=False,
        via="launch-prompt",
        added_by="t@two",
        words=frozenset(),
    )

    code, output = refused(custom="cleo")

    assert code == 2
    assert f"{REPO}-two-cleo@{OTHER_HOST}" in output


def test_given_the_short_and_long_name_flags_when_parsed_then_they_mean_the_same(monkeypatch):
    seen: list[dict] = []

    def capture(ctx, **options):
        seen.append({"options": options, "args": list(ctx.args)})

    command = main.click.command(context_settings=main.SESSION_CONTEXT)(main._session_options(capture))
    from click.testing import CliRunner

    runner = CliRunner()
    runner.invoke(command, ["-N", "cleo", "probe", "--engine-flag"])
    runner.invoke(command, ["--name", "cleo", "probe", "--engine-flag"])

    assert seen[0]["options"]["human_name"] == seen[1]["options"]["human_name"] == "cleo"
    assert seen[0]["options"]["name"] == "probe"
    for parsed in seen:
        assert "--name" not in parsed["args"] and "-N" not in parsed["args"] and "cleo" not in parsed["args"]
        assert parsed["args"] == ["--engine-flag"]


def test_given_a_name_and_a_label_when_launched_then_the_title_is_stem_label_name(fleet):
    (_, title), _ = resolve("probe", custom="cleo")

    assert title == f"{STEM}-probe-cleo"


def test_given_a_positional_name_this_host_holds_when_launched_then_it_reattaches_without_reserving(fleet):
    (_, held_title), _ = resolve(custom="cleo")
    before = fleet.tip()

    (session_id, title), _ = resolve("cleo")

    assert title == held_title == f"{STEM}-cleo"
    assert session_id == f"c-{title}"
    assert fleet.tip() == before


def test_given_a_positional_name_held_elsewhere_when_launched_then_it_exits_two_naming_the_holder_and_the_flag(fleet):
    other = fleet.other_machine()
    reg.reserve_hand_chosen(
        other,
        "cleo",
        reg.Slot(repo=REPO, repo_alias=REPO, host_alias="two", label=None, machine=OTHER_HOST, client="claude-code"),
        add=False,
        via="launch-prompt",
        added_by="t@two",
        words=frozenset(),
    )
    before = fleet.tip()

    code, output = refused("cleo")

    assert code == 2
    assert f"@{OTHER_HOST}" in output
    assert "--name cleo" in output
    assert fleet.tip() == before


def test_given_a_free_positional_name_when_launched_then_it_exits_two_printing_the_name_flag(fleet):
    code, output = refused("cleo")

    assert code == 2
    assert "--name cleo" in output


def test_given_an_all_digits_positional_when_launched_then_it_is_an_index_slot_as_before(fleet):
    before = fleet.tip()

    (session_id, title), _ = resolve("3")

    assert (session_id, title) == (f"c-{STEM}-3", f"{STEM}-3")
    assert fleet.tip() == before


def test_given_a_drop_title_held_here_when_launched_again_then_it_reattaches(fleet):
    resolve("probe", no_name=True)
    before = fleet.tip()

    (_, title), _ = resolve("probe", no_name=True)

    assert title == f"{STEM}-probe"
    assert fleet.tip() == before


def test_given_a_drop_title_held_on_another_machine_when_launched_then_it_exits_two_naming_the_holder(fleet):
    other = fleet.other_machine()
    reg.reserve_label(
        other,
        reg.Slot(repo=REPO, repo_alias=REPO, host_alias=HOST_ALIAS, label="probe", machine=OTHER_HOST, client="codex"),
        "probe",
    )

    code, output = refused("probe", no_name=True)

    assert code == 2
    assert f"{STEM}-probe@{OTHER_HOST}" in output


def test_given_a_labels_last_token_held_by_a_named_session_when_dropped_then_it_exits_two(fleet):
    resolve(custom="cleo")

    code, output = refused("team-cleo", no_name=True)

    assert code == 2
    assert "cleo" in output


def test_given_a_held_label_row_when_its_token_is_chosen_as_a_name_then_it_is_refused_naming_the_holder(fleet):
    resolve("probe", no_name=True)

    code, output = refused(custom="probe")

    assert code == 2
    assert f"{STEM}-probe@{HOST}" in output


def test_given_a_held_label_row_when_its_title_is_parsed_then_the_whole_label_is_returned_with_no_name(fleet):
    resolve("probe", no_name=True)
    names = reg.known_names(fleet.registry)

    parsed = session_title.parse_title(f"{STEM}-probe", STEM, names)

    assert "probe" not in names
    assert parsed == session_title.ParsedTitle(STEM, "probe", None, None)


def test_given_a_held_session_name_when_its_title_is_parsed_then_the_name_is_split_off_the_label(fleet):
    resolve("probe", custom="cleo")
    names = reg.known_names(fleet.registry)

    assert session_title.parse_title(f"{STEM}-probe-cleo", STEM, names) == session_title.ParsedTitle(
        STEM, "probe", "cleo", None
    )
    assert session_title.parse_title(f"{STEM}-probe-3", STEM, names) == session_title.ParsedTitle(
        STEM, "probe", None, 3
    )


@pytest.mark.parametrize(
    ("value", "reason"),
    [("clea", "one edit from the held name cleo"), ("sun", "common English word")],
)
def test_given_a_confusable_or_word_name_without_a_terminal_when_chosen_then_it_is_refused(fleet, value, reason):
    resolve(custom="cleo")
    before = fleet.tip()

    code, output = refused(custom=value)

    assert code == 2
    assert reason in output
    assert fleet.tip() == before


def test_given_the_registry_unreachable_when_a_name_is_chosen_then_it_refuses_without_taking_a_spare(fleet):
    resolve()  # reserves a name and tops up this machine's spare
    fleet.offline()

    code, output = refused(custom="cleo")

    assert code == 75
    assert "cannot be proved free" in output
    assert reg.read_spool() == {}


def test_given_names_disabled_when_launched_then_index_names_are_allocated_as_before(fleet, monkeypatch):
    monkeypatch.setenv("AI_CLI_SESSION_NAMES", "0")
    LIVE_TMUX.append(f"c-{STEM}-review-1")
    before = fleet.tip()

    (session_id, title), _ = resolve("review")

    assert (session_id, title) == (f"c-{STEM}-review-2", f"{STEM}-review-2")
    assert fleet.tip() == before


def test_given_names_disabled_when_a_name_is_chosen_then_it_is_still_honoured_through_the_registry(fleet, monkeypatch):
    monkeypatch.setenv("AI_CLI_SESSION_NAMES", "0")

    (_, title), _ = resolve(custom="cleo")

    assert title == f"{STEM}-cleo"
    assert fleet.rows()["cleo"]["machine"] == HOST
