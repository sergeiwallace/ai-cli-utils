"""``ai ps``, ``ai layout`` and ``ai sync`` are declared in Click and keep every form they accepted.

The parity tables below are the inventory of every action and flag token the hand
parsers recognised before they were declared in Click (``process_hygiene.cmd_ps``,
``layout.run_layout_command``, ``main.cmd_sync`` and, in ``sync.py``, ``_parse_flags``,
``sync_resolve`` and ``sync_watch``). Each row names the form a user or a caller types
and the values the unchanged implementation receives.
"""

import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import run_cli

from ai_cli import sync

REPO = Path(__file__).resolve().parents[1]
CONFIG = {"process_hygiene": {}}


def _ps_rows():
    for action in ([], ["clean"], ["cron"]):
        for typed, passed in (
            ([], []),
            (["-f"], ["--force"]),
            (["--force"], ["--force"]),
            (["-r"], ["--refresh"]),
            (["--refresh"], ["--refresh"]),
            (["-f", "-r"], ["--force", "--refresh"]),
            (["--refresh", "--force"], ["--force", "--refresh"]),
        ):
            yield ["ps", *action, *typed], [*action, *passed]


@pytest.mark.parametrize(("argv", "passed"), list(_ps_rows()), ids=lambda v: " ".join(v) if v else "-")
def test_given_an_ai_ps_form_when_invoked_then_cmd_ps_receives_the_same_action_and_flags(argv, passed):
    with patch("ai_cli.process_hygiene.cmd_ps", return_value=0) as impl:
        code, _, err = run_cli(["ai", *argv], config=CONFIG)

    assert code == 0, err
    impl.assert_called_once_with(passed, CONFIG)


LAYOUT_ROWS = [
    (["layout", "list"], "cmd_layout_list", ()),
    (["layout", "validate", "mylayout"], "cmd_layout_validate", ("mylayout",)),
    (["layout", "profiles", "mylayout"], "cmd_layout_profiles", ("mylayout",)),
    (["layout", "mylayout"], "cmd_layout_apply", ("mylayout",)),
    (["layout", "dev-work"], "cmd_layout_apply", ("dev-work",)),
    (["layout", "_apply"], "cmd_layout_apply", ("_apply",)),
]


@pytest.mark.parametrize(
    ("argv", "target", "args"), LAYOUT_ROWS, ids=lambda v: " ".join(v) if isinstance(v, list) else None
)
def test_given_an_ai_layout_form_when_invoked_then_the_same_layout_function_runs(argv, target, args):
    with patch(f"ai_cli.layout.{target}", return_value=0) as impl:
        code, _, err = run_cli(["ai", *argv])

    assert code == 0, err
    impl.assert_called_once_with(*args)


_COMMON = [
    (["-m"], ["--memories-only"]),
    (["--memories-only"], ["--memories-only"]),
    (["-n"], ["--dry-run"]),
    (["--dry-run"], ["--dry-run"]),
    (["-v"], ["--verbose"]),
    (["--verbose"], ["--verbose"]),
    (["-f"], ["--force"]),
    (["--force"], ["--force"]),
    (["-m", "-n", "-v", "-f"], ["--memories-only", "--dry-run", "--verbose", "--force"]),
]
_DRY_VERBOSE = [
    ([], []),
    (["-n"], ["--dry-run"]),
    (["--dry-run"], ["--dry-run"]),
    (["-v"], ["--verbose"]),
    (["--verbose"], ["--verbose"]),
]

SYNC_ROWS = [
    *((["push", *typed], "sync_push", passed) for typed, passed in [([], []), *_COMMON]),
    *(
        (["pull", *typed], "sync_pull", passed)
        for typed, passed in [
            ([], []),
            *_COMMON,
            (["--prefer-remote"], ["--prefer-remote"]),
            (["-p"], ["--prefer-remote"]),
        ]
    ),
    (["conflicts"], "sync_conflicts", []),
    *((["resolve", *typed], "sync_resolve", passed) for typed, passed in _DRY_VERBOSE),
    *(
        (["watch", *typed], "sync_watch", passed)
        for typed, passed in [
            ([], []),
            (["--auto"], ["--auto"]),
            (["-a"], ["--auto"]),
            (["--verbose"], ["--verbose"]),
            (["-v"], ["--verbose"]),
        ]
    ),
]


@pytest.mark.parametrize(
    ("argv", "target", "passed"), SYNC_ROWS, ids=lambda v: " ".join(v) if isinstance(v, list) else None
)
def test_given_an_ai_sync_form_when_invoked_then_the_same_sync_function_receives_the_same_flags(argv, target, passed):
    with patch(f"ai_cli.sync.{target}", return_value=0) as impl:
        code, _, err = run_cli(["ai", "sync", *argv])

    assert code == 0, err
    impl.assert_called_once_with(passed)
    if target in ("sync_push", "sync_pull") and "-p" not in argv:
        # The flags reach the unchanged hand parser, which must read them as the typed form.
        assert sync._parse_flags(passed) == sync._parse_flags(argv[1:])


@pytest.mark.parametrize(
    ("typed", "dry_run", "verbose"), [(t, "--dry-run" in p, "--verbose" in p) for t, p in _DRY_VERBOSE]
)
def test_given_an_ai_sync_cleanup_form_when_invoked_then_cleanup_receives_the_same_switches(typed, dry_run, verbose):
    with (
        patch("ai_cli.sync._cc_projects_dir", return_value=Path("projects")),
        patch("ai_cli.sync.get_local_prefix", return_value="prefix"),
        patch("ai_cli.sync.clean_worktree_cc_dirs", return_value=(0, 0)) as impl,
    ):
        code, _, err = run_cli(["ai", "sync", "cleanup", *typed])

    assert code == 0, err
    impl.assert_called_once_with(Path("projects"), "prefix", dry_run=dry_run, verbose=verbose)


@pytest.mark.parametrize(
    ("typed", "dry_run", "verbose"), [(t, "--dry-run" in p, "--verbose" in p) for t, p in _DRY_VERBOSE]
)
def test_given_an_ai_sync_repair_worktree_form_when_invoked_then_repair_receives_the_same_values(
    typed, dry_run, verbose
):
    with (
        patch("ai_cli.sync._cc_projects_dir", return_value=Path("projects")),
        patch("ai_cli.sync.get_local_prefix", return_value="prefix"),
        patch("ai_cli.sync.repair_worktree_cc_dir", return_value=1) as impl,
    ):
        code, _, err = run_cli(["ai", "sync", "repair-worktree", "myproject", "wt-1", *typed])

    assert code == 0, err
    impl.assert_called_once_with(
        project_name="myproject",
        wt_name="wt-1",
        cc_projects_dir=Path("projects"),
        local_prefix="prefix",
        dry_run=dry_run,
        verbose=verbose,
    )


IMPLEMENTATIONS = [
    "ai_cli.process_hygiene.cmd_ps",
    "ai_cli.layout.run_layout_command",
    *(f"ai_cli.sync.{name}" for name in ("sync_push", "sync_pull", "sync_conflicts", "sync_resolve", "sync_watch")),
    "ai_cli.sync.clean_worktree_cc_dirs",
    "ai_cli.sync.repair_worktree_cc_dir",
]


def _run_stubbed(argv):
    patches = [patch(target, return_value=0) for target in IMPLEMENTATIONS]
    mocks = [p.start() for p in patches]
    try:
        return run_cli(["ai", *argv], config=CONFIG), mocks
    finally:
        for p in patches:
            p.stop()


@pytest.mark.parametrize(
    ("argv", "listed"),
    [
        (["ps"], ["clean", "cron", "-f, --force", "-r, --refresh"]),
        (["layout"], ["list", "validate", "profiles", "NAME"]),
        (["sync"], ["push", "pull", "conflicts", "resolve", "watch", "cleanup", "repair-worktree"]),
        (["sync", "pull"], ["-p, --prefer-remote", "-m, --memories-only"]),
        (["sync", "watch"], ["-a, --auto", "-v, --verbose"]),
    ],
    ids=lambda v: " ".join(v) if v and not v[0].startswith("-") and len(v) < 3 else None,
)
def test_given_a_self_parsing_command_when_h_is_passed_then_click_help_lists_it_and_nothing_runs(argv, listed):
    (code, out, err), mocks = _run_stubbed([*argv, "-h"])

    assert code == 0, err
    assert out.startswith(f"Usage: ai {' '.join(argv)} ")
    assert [item for item in listed if item not in out] == []
    assert [m for m in mocks if m.called] == []


@pytest.mark.parametrize(
    "argv",
    [
        ["layout", "validate"],
        ["layout", "profiles"],
        ["sync", "repair-worktree"],
        ["sync", "repair-worktree", "myproject"],
    ],
    ids=" ".join,
)
def test_given_a_required_name_is_missing_when_invoked_then_it_exits_non_zero_with_usage(argv):
    (code, _, err), mocks = _run_stubbed(argv)

    assert code != 0
    assert "Usage: ai " in err
    assert "Missing argument" in err
    assert [m for m in mocks if m.called] == []


@pytest.mark.parametrize(
    ("argv", "named"),
    [
        (["ps", "bogus"], "bogus"),
        (["ps", "-x"], "-x"),
        (["ps", "clean", "extra"], "extra"),
        (["ps", "clean", "--everything"], "--everything"),
        (["ps", "-f", "clean"], "-f/--force"),
        (["layout", "validate", "mylayout", "extra"], "extra"),
        (["layout", "mylayout", "extra"], "extra"),
        (["layout", "-x"], "-x"),
        (["sync", "bogus"], "bogus"),
        (["sync", "push", "--nope"], "--nope"),
        (["sync", "watch", "extra"], "extra"),
        (["sync", "conflicts", "-n"], "-n"),
        (["sync", "repair-worktree", "myproject", "wt-1", "extra"], "extra"),
    ],
    ids=lambda v: " ".join(v) if isinstance(v, list) else None,
)
def test_given_an_unknown_action_option_or_trailing_token_when_invoked_then_a_usage_error_names_it(argv, named):
    (code, _, err), mocks = _run_stubbed(argv)

    assert code != 0
    assert named in err
    assert [m for m in mocks if m.called] == []


# An invocation is a line where `ai ps|layout|sync ...` is followed by `&`, `|`, `>` or `)`,
# or any such line in a shell file: prose that merely names a command is not one, and prose
# ends a clause with `;` often enough that `;` is left out.
_INVOCATION = re.compile(r"(?:^|[\s;&|(`])ai ((?:ps|layout|sync)(?: [\w./-]+)*)\s*(?=[&|>)])")
_SHELL_LINE = re.compile(r"(?:^|[\s;&|(`])ai ((?:ps|layout|sync)(?: [\w./-]+)*)\s*$")


def _repository_invocations():
    tracked = subprocess.run(
        ["git", "ls-files", "src", "scripts", ".githooks", "config"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    found = set()
    for rel in tracked:
        path = REPO / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        shell = path.suffix in (".sh", ".bash", ".zsh") or rel.startswith(".githooks/")
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            for match in _INVOCATION.finditer(line):
                found.add((rel, tuple(match.group(1).split())))
            if shell:
                for match in _SHELL_LINE.finditer(line):
                    found.add((rel, tuple(match.group(1).split())))
    return sorted(found)


def test_given_the_repository_when_searched_then_the_known_callers_are_found():
    found = {argv for _, argv in _repository_invocations()}

    assert ("ps", "cron") in found
    assert ("sync", "watch", "--auto") in found


@pytest.mark.parametrize(
    ("source", "argv"), _repository_invocations(), ids=lambda v: " ".join(v) if isinstance(v, tuple) else v
)
def test_given_a_repository_caller_when_its_invocation_is_parsed_then_it_still_runs(source, argv):
    (code, _, err), mocks = _run_stubbed(list(argv))

    assert code == 0, f"{source}: ai {' '.join(argv)}: {err}"
    assert [m for m in mocks if m.called] != []
