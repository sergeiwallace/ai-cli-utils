"""Migrating sessions to human names (T-3.8).

The migration runs for real on the migrate suite's ``slot`` fixture, a registered
``.worktrees/myrepo-3`` carrying every artifact a rename moves, against a bare-remote
name registry.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from name_fleet import HOST, build_fleet, git
from test_session_migrate import OLD, run_cli, slot  # noqa: F401  (slot is a fixture)

from ai_cli import main
from ai_cli import name_registry as reg

STEM = "myrepo-box"  # the legacy slot myrepo-3 predates the host alias in names


@pytest.fixture
def fleet(tmp_path, monkeypatch, slot):  # noqa: F811
    (tmp_path / "fleet").mkdir()
    fleet = build_fleet(tmp_path / "fleet", monkeypatch)
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    monkeypatch.setattr("ai_cli.main._migration_stem", lambda repo_root: STEM)
    monkeypatch.setattr("ai_cli.session_naming.resolve_host_alias", lambda: "box")
    fleet.slot = slot
    return fleet


def migrate(fleet, *args):
    with patch("ai_cli.config._get_projects_dir", return_value=fleet.projects):
        return run_cli(["ai", "migrate-session", *args, "-r", str(fleet.slot["repo"]), "-y"])


def test_given_a_stopped_session_when_migrated_to_a_chosen_name_then_the_reserving_commit_carries_the_intent(fleet):
    code, out, err = migrate(fleet, OLD, "--to-name", "cleo")

    assert code == 0, out + err
    new = f"{STEM}-cleo"
    assert (fleet.slot["repo"] / ".worktrees" / new).is_dir()
    record = json.loads(fleet.file(f"migrations/{OLD}.json"))
    assert record["status"] == "complete" and record["new_title"] == new
    history = git(fleet.remote, "log", "--format=%H", "refs/heads/fleet-session-names/registry").splitlines()
    reserving = next(commit for commit in history if "names/cleo.json" in fleet.changed(commit))
    assert f"migrations/{OLD}.json" in fleet.changed(reserving)
    assert json.loads(git(fleet.remote, "show", f"{reserving}:migrations/{OLD}.json"))["status"] == "intent"
    assert fleet.rows()["cleo"]["source"] == "migrated"


def test_given_a_refusal_check_failing_when_migrated_to_a_name_then_nothing_is_reserved(fleet):
    (fleet.slot["repo"] / ".worktrees" / f"{STEM}-cleo").mkdir()
    before = fleet.tip()

    code, _out, err = migrate(fleet, OLD, "--to-name", "cleo")

    assert code != 0
    assert fleet.tip() == before


def test_given_no_name_when_migrated_then_the_shortest_free_name_is_picked(fleet):
    code, out, err = migrate(fleet, OLD, "--to-name")

    assert code == 0, out + err
    [token] = [t for t, row in fleet.rows().items() if row.get("source") == "migrated"]
    assert len(token) == 3


@pytest.mark.parametrize("bound", ["2", "5"])
def test_given_a_max_length_outside_the_limits_when_migrated_then_it_exits_two_before_anything(fleet, bound):
    before = fleet.tip()

    code, out, err = migrate(fleet, OLD, "--to-name", "--max-length", bound)

    assert code == 2
    assert bound in out + err and "3 to 4 letters" in out + err
    assert fleet.tip() == before


def test_given_a_max_length_inside_the_limits_when_migrated_then_the_name_is_no_longer(tmp_path, monkeypatch, slot):  # noqa: F811
    (tmp_path / "fleet").mkdir()
    fleet = build_fleet(tmp_path / "fleet", monkeypatch, names=["bert", "cleo", "dora"])
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    monkeypatch.setattr("ai_cli.main._migration_stem", lambda repo_root: STEM)
    fleet.slot = slot

    code, out, err = migrate(fleet, OLD, "--to-name", "--max-length", "3")

    assert code != 0
    assert "no free session name" in out + err


def test_given_the_rename_failing_with_nothing_left_when_migrated_then_the_row_and_intent_are_released(
    fleet, monkeypatch
):
    real = main._migrate_session_for_names

    def fail_before_writing(repo_root, old, new, **kwargs):
        if not kwargs.get("dry_run"):
            raise RuntimeError("simulated failure before the first write")
        return real(repo_root, old, new, **kwargs)

    monkeypatch.setattr(main, "_migrate_session_for_names", fail_before_writing)

    code, out, err = migrate(fleet, OLD, "--to-name", "cleo")

    assert code != 0
    assert "was released" in out + err
    assert "cleo" not in fleet.rows()
    assert fleet.file(f"migrations/{OLD}.json") is None


def test_given_the_rename_failing_half_way_when_migrated_then_the_row_is_held_incomplete_and_a_rerun_resumes(
    fleet, monkeypatch
):
    real = main._migrate_session_for_names
    calls = {"n": 0}

    def fail_after_moving(repo_root, old, new, **kwargs):
        if kwargs.get("dry_run"):
            return real(repo_root, old, new, **kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            git(repo_root, "worktree", "move", str(repo_root / ".worktrees" / old), str(repo_root / ".worktrees" / new))
            raise RuntimeError("simulated failure after the worktree moved")
        return real(repo_root, old, new, **kwargs)

    monkeypatch.setattr(main, "_migrate_session_for_names", fail_after_moving)

    code, out, err = migrate(fleet, OLD, "--to-name", "cleo")

    assert code != 0
    row = fleet.rows()["cleo"]
    assert row["state"] == "migration-incomplete"
    assert any(item["kind"] == "worktree" for item in row["recovery"])
    assert json.loads(fleet.file(f"migrations/{OLD}.json"))["status"] == "intent"
    release = CliRunner().invoke(main._cli_group, ["release-name", "cleo"])
    assert release.exit_code == 2

    code, out, err = migrate(fleet, OLD, "--to-name", "cleo")

    assert code == 0, out + err
    assert fleet.rows()["cleo"]["state"] == "active"
    assert json.loads(fleet.file(f"migrations/{OLD}.json"))["status"] == "complete"


def test_given_an_intent_whose_rename_finished_when_reconciled_then_it_completes_once(fleet, monkeypatch):
    real = main._complete_migration_for_names
    monkeypatch.setattr(
        main,
        "_complete_migration_for_names",
        lambda *a, **k: (_ for _ in ()).throw(reg.RegistryContendedError("simulated")),
    )
    code, _out, _err = migrate(fleet, OLD, "--to-name", "cleo")
    assert code != 0
    monkeypatch.setattr(main, "_complete_migration_for_names", real)

    first = CliRunner().invoke(main._cli_group, ["reconcile-names"])
    tip = fleet.tip()
    second = CliRunner().invoke(main._cli_group, ["reconcile-names"])

    assert first.exit_code == 0, first.output
    assert json.loads(fleet.file(f"migrations/{OLD}.json"))["status"] == "complete"
    assert second.exit_code == 0
    assert fleet.tip() == tip


def test_given_an_integer_new_name_when_migrated_then_it_behaves_as_before(fleet):
    before = fleet.tip()

    code, out, err = migrate(fleet, OLD, "myrepo-7")

    assert code == 0, out + err
    assert (fleet.slot["repo"] / ".worktrees" / "myrepo-7").is_dir()
    assert fleet.tip() == before


def test_given_a_human_new_name_without_to_name_when_migrated_then_it_is_refused(fleet):
    code, out, err = migrate(fleet, OLD, f"{STEM}-cleo")

    assert code != 0
    assert "--to-name" in out + err


def test_given_the_host_sweep_without_yes_when_run_then_it_lists_every_index_named_session_and_writes_nothing(fleet):

    before = fleet.tip()
    with patch("ai_cli.config._get_projects_dir", return_value=fleet.projects):
        code, out, err = run_cli(["ai", "migrate-session", "--all", "--to-names", "-r", str(fleet.slot["repo"])])

    assert code == 0, out + err
    assert OLD in out
    assert "stopped" in out
    assert fleet.tip() == before


def sweep(fleet, *flags):

    with patch("ai_cli.config._get_projects_dir", return_value=fleet.projects):
        return run_cli(["ai", "migrate-session", "--all", "--to-names", "-r", str(fleet.slot["repo"]), *flags])


def test_given_the_host_sweep_with_yes_when_one_session_runs_then_the_stopped_one_migrates_and_it_exits_non_zero(
    fleet, monkeypatch
):
    repo = fleet.slot["repo"]
    git(repo, "worktree", "add", "-q", str(repo / ".worktrees" / "myrepo-4"), "-b", "wt-myrepo-4")
    monkeypatch.setattr("ai_cli.session._tmux_session_names", lambda: ["c-myrepo-4"])

    code, out, err = sweep(fleet, "-y")

    assert code == 1
    assert "myrepo-4 is running" in out + err
    assert not (repo / ".worktrees" / OLD).exists()
    assert any(row.get("source") == "migrated" for row in fleet.rows().values())


def test_given_a_session_whose_worktree_is_gone_when_swept_then_its_transcript_is_retitled_and_no_worktree_is_made(
    fleet, monkeypatch
):
    import shutil

    from ai_cli.cc_migrate import cc_project_dir

    repo = fleet.slot["repo"]
    gone = repo / ".worktrees" / "myrepo-5"
    project = cc_project_dir(gone, fleet.slot["claude"])
    project.mkdir(parents=True)
    transcript = project / "55555555-0000-4000-8000-000000000005.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "cwd": str(gone), "sessionId": "keep-me"})
        + "\n"
        + json.dumps({"type": "custom-title", "customTitle": "myrepo-5", "sessionId": "keep-me"})
        + "\n"
    )
    shutil.rmtree(repo / ".worktrees" / OLD, ignore_errors=True)
    git(repo, "worktree", "prune")

    code, out, err = sweep(fleet, "-y")

    assert code == 0, out + err
    records = [json.loads(line) for line in transcript.read_text().splitlines()]
    [row] = [row for row in fleet.rows().values() if row.get("title") == records[1]["customTitle"]]
    assert row["source"] == "migrated"
    assert row["worktree"] is None
    assert not gone.exists()
    assert not (repo / ".worktrees" / row["title"]).exists()
    assert all(record["sessionId"] == "keep-me" for record in records)


def test_given_an_unreadable_client_record_when_swept_then_it_exits_non_zero_naming_the_record(fleet):
    from pathlib import Path

    bad = Path.home() / ".codex" / "sessions" / "2026" / "10" / "23" / "rollout-2026-10-23T00-00-00-bad.jsonl"
    bad.parent.mkdir(parents=True)
    bad.write_text("not json\n")

    code, out, err = sweep(fleet)

    assert code == 1
    assert str(bad) in out + err


def test_given_a_human_named_session_over_the_limit_when_swept_over_limit_then_it_gets_an_in_limit_name_and_the_old_row_goes(
    fleet, monkeypatch
):

    repo = fleet.slot["repo"]
    long_title = f"{STEM}-rebecca"
    with patch("ai_cli.config._get_projects_dir", return_value=fleet.projects):
        assert run_cli(["ai", "migrate-session", OLD, "myrepo-8", "-r", str(repo), "-y"])[0] == 0
    row = reg.session_row(
        name="rebecca",
        title=long_title,
        repo=repo.name,
        repo_alias="myrepo",
        host_alias="box",
        label=None,
        machine=HOST,
        client="claude-code",
        source="custom",
        tier=None,
        worktree=f".worktrees/{long_title}",
    )
    fleet.registry.transact(lambda s: reg.Change({reg.row_path("rebecca"): reg.checked_row(row)}, "row (fixture)"))
    with patch("ai_cli.config._get_projects_dir", return_value=fleet.projects):
        assert (
            run_cli(["ai", "migrate-session", "myrepo-8", long_title, "-r", str(repo), "-y"])[0] != 0
        )  # a hand-typed human name is refused
    git(repo, "worktree", "move", str(repo / ".worktrees" / "myrepo-8"), str(repo / ".worktrees" / long_title))

    listed = CliRunner().invoke(main._cli_group, ["check-names", "-j"])
    over = {item["title"] for item in json.loads(listed.output.strip().splitlines()[-1])["over_limit"]}

    code, out, err = sweep(fleet, "--over-limit")
    assert code == 0, out + err
    assert over == {long_title}
    assert long_title in out

    code, out, err = sweep(fleet, "--over-limit", "-y")

    assert code == 0, out + err
    rows = fleet.rows()
    assert "rebecca" not in rows
    [renamed] = [r for r in rows.values() if r.get("source") == "migrated"]
    assert len(renamed["token"]) <= 4


def test_given_an_intent_with_every_artifact_still_under_the_old_title_when_reconciled_then_nothing_changes(fleet):
    record = reg.migration_record(OLD, f"{STEM}-cleo", HOST, "claude-code")
    row = reg.session_row(
        name="cleo",
        title=f"{STEM}-cleo",
        repo=fleet.slot["repo"].name,
        repo_alias="myrepo",
        host_alias="box",
        label=None,
        machine=HOST,
        client="claude-code",
        source="migrated",
        tier=None,
        worktree=f".worktrees/{STEM}-cleo",
    )
    fleet.registry.transact(
        lambda s: reg.Change(
            {reg.row_path("cleo"): reg.checked_row(row), reg.migration_path(OLD): reg.encode_json(record)},
            "intent (fixture)",
        )
    )

    with patch("ai_cli.name_commands._projects_root", return_value=fleet.slot["repo"].parent):
        result = CliRunner().invoke(main._cli_group, ["reconcile-names"])

    assert result.exit_code == 0, result.output
    assert json.loads(fleet.file(f"migrations/{OLD}.json")) == record
    assert fleet.rows()["cleo"] == row
    check = CliRunner().invoke(main._cli_group, ["check-names"])
    assert check.exit_code == 0


def test_given_an_intent_with_artifacts_under_both_titles_when_reconciled_then_the_row_is_held_incomplete(fleet):
    repo = fleet.slot["repo"]
    git(repo, "worktree", "add", "-q", str(repo / ".worktrees" / f"{STEM}-cleo"), "-b", f"wt-{STEM}-cleo")
    record = reg.migration_record(OLD, f"{STEM}-cleo", HOST, "claude-code")
    row = reg.session_row(
        name="cleo",
        title=f"{STEM}-cleo",
        repo=repo.name,
        repo_alias="myrepo",
        host_alias="box",
        label=None,
        machine=HOST,
        client="claude-code",
        source="migrated",
        tier=None,
        worktree=f".worktrees/{STEM}-cleo",
    )
    fleet.registry.transact(
        lambda s: reg.Change(
            {reg.row_path("cleo"): reg.checked_row(row), reg.migration_path(OLD): reg.encode_json(record)},
            "intent (fixture)",
        )
    )

    with patch("ai_cli.name_commands._projects_root", return_value=repo.parent):
        result = CliRunner().invoke(main._cli_group, ["reconcile-names"])

    assert result.exit_code == 0, result.output
    assert fleet.rows()["cleo"]["state"] == "migration-incomplete"
    assert json.loads(fleet.file(f"migrations/{OLD}.json"))["status"] == "intent"


def test_given_a_session_adopted_with_a_name_when_adopted_then_the_name_is_reserved_and_index_adoption_is_unchanged(
    fleet, tmp_path
):

    from ai_cli.cc_migrate import cc_project_dir

    repo = fleet.slot["repo"]
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    project = cc_project_dir(repo, fleet.slot["claude"])
    project.mkdir(parents=True, exist_ok=True)
    (project / "66666666-0000-4000-8000-000000000006.jsonl").write_text(
        json.dumps({"type": "user", "cwd": str(repo)})
        + "\n"
        + json.dumps({"type": "custom-title", "customTitle": "myrepo-9"})
        + "\n"
    )

    with patch("ai_cli.config._get_projects_dir", return_value=fleet.projects):
        code, out, err = run_cli(["ai", "adopt-session", "myrepo-9", "-r", str(repo), "-m", "dora", "-y"])

    assert code == 0, out + err
    assert fleet.rows()["dora"]["title"] == f"{STEM}-dora"
    assert (repo / ".worktrees" / f"{STEM}-dora").is_dir()
