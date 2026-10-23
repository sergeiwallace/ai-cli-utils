"""Releasing names, moving them between hosts, and the over-limit check (T-3.11, T-3.9, T-3.1, T-3.3).

A row is released only by the machine that holds it, and only when its worktree and its
client transcript are both proved gone. Everything runs against a real bare remote.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from click.testing import CliRunner
from name_fleet import HOST, OTHER_HOST, REPO, STEM, build_fleet, git, policy_commit, policy_of, publish_list

from ai_cli import name_registry as reg
from ai_cli import session_clients
from ai_cli.cc_migrate import cc_project_dir
from ai_cli.main import _cli_group


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    fleet = build_fleet(tmp_path, monkeypatch)
    monkeypatch.setattr("ai_cli.session._tmux_session_names", list)
    monkeypatch.setattr("ai_cli.session_naming.resolve_host_alias", lambda: "box")
    return fleet


def _repo(fleet):
    """A repository under the projects directory with one session worktree, and its Claude Code store."""
    repo = fleet.projects / REPO
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    (Path.home() / ".claude" / "projects").mkdir(parents=True, exist_ok=True)
    return repo


def _session(fleet, name: str, *, machine: str = HOST, worktree: bool = True, label: str | None = None) -> dict:
    registry = fleet.registry if machine == HOST else fleet.other_machine(f"m-{name}")
    slot = reg.Slot(
        repo=REPO,
        repo_alias=REPO,
        host_alias="box",
        label=label,
        machine=machine,
        client="claude-code",
        worktree=worktree,
    )
    reservation = reg.reserve_hand_chosen(
        registry, name, slot, add=True, via="add-name", added_by="t@box", words=frozenset()
    )
    return reservation.row


def _add_worktree(repo, title: str):
    path = repo / ".worktrees" / title
    git(repo, "worktree", "add", "-q", str(path), "-b", f"wt-{title}")
    return path


def _add_transcript(path_or_repo, title: str, *, cwd=None):
    target = cwd or path_or_repo
    project = cc_project_dir(Path(target).resolve(strict=False), Path.home() / ".claude")
    project.mkdir(parents=True, exist_ok=True)
    (project / "s.jsonl").write_text(
        json.dumps({"type": "custom-title", "customTitle": title, "cwd": str(target)}) + "\n"
    )
    return project


def reconcile(*args) -> tuple[int, dict]:
    result = CliRunner().invoke(_cli_group, ["reconcile-names", "-j", *args])
    return result.exit_code, (
        json.loads(result.output.strip().splitlines()[-1]) if result.exit_code == 0 else {"out": result.output}
    )


# --- T-3.11: what releases a row -------------------------------------------------------------------


def test_given_worktree_and_transcript_both_gone_when_reconciled_then_the_row_is_deleted_in_one_push(fleet):
    _repo(fleet)
    _session(fleet, "cleo")

    code, summary = reconcile()

    assert code == 0, summary
    assert "cleo" in summary["released"]
    assert "cleo" not in fleet.rows()
    assert "names/cleo.json" in fleet.changed()


def test_given_the_worktree_present_when_reconciled_then_the_row_stays(fleet):
    repo = _repo(fleet)
    _session(fleet, "cleo")
    _add_worktree(repo, f"{STEM}-cleo")

    code, summary = reconcile()

    assert code == 0
    assert "cleo" in fleet.rows()
    assert "worktree present" in summary["held"]["cleo"]


def test_given_the_worktree_removed_from_disk_but_still_registered_when_reconciled_then_the_row_stays(fleet):
    repo = _repo(fleet)
    _session(fleet, "cleo")
    path = _add_worktree(repo, f"{STEM}-cleo")
    import shutil

    shutil.rmtree(path)

    reconcile()

    assert "cleo" in fleet.rows()


def test_given_the_worktree_list_failing_when_reconciled_then_the_row_stays_as_unknown(fleet, monkeypatch):
    (fleet.projects / REPO).mkdir()
    (Path.home() / ".claude" / "projects").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(fleet.projects))
    _session(fleet, "cleo")

    _code, summary = reconcile()

    assert "cleo" in fleet.rows()
    assert "worktree unknown: `git worktree list` failed" in summary["held"]["cleo"]


def test_given_the_transcript_present_when_reconciled_then_the_row_stays(fleet):
    repo = _repo(fleet)
    _session(fleet, "cleo")
    _add_transcript(repo, f"{STEM}-cleo", cwd=repo / ".worktrees" / f"{STEM}-cleo")

    code, summary = reconcile()

    assert "cleo" in fleet.rows()
    assert "transcript present" in summary["held"]["cleo"]


def test_given_no_claude_code_store_when_reconciled_then_the_row_stays_as_unknown(fleet):
    import shutil

    _repo(fleet)
    _session(fleet, "cleo")
    shutil.rmtree(Path.home() / ".claude" / "projects")

    code, summary = reconcile()

    assert "cleo" in fleet.rows()
    assert "transcript unknown" in summary["held"]["cleo"]


def test_given_every_supported_client_when_listed_then_each_has_a_transcript_probe():
    from ai_cli import session_title

    assert set(session_title.CLIENTS.values()) == set(session_clients.ADAPTERS)


def test_given_a_title_when_reconciled_then_only_that_row_is_considered(fleet):
    _repo(fleet)
    _session(fleet, "cleo")
    _session(fleet, "dora")

    reconcile("-t", f"{STEM}-cleo")

    assert "cleo" not in fleet.rows()
    assert "dora" in fleet.rows()


def test_given_another_machines_row_when_reconciled_then_it_is_never_touched(fleet):
    _repo(fleet)
    _session(fleet, "cleo", machine=OTHER_HOST)

    _code, planned = reconcile("-d")
    code, summary = reconcile()

    assert "cleo" not in planned["released"] and "cleo" not in planned["held"]
    assert "cleo" in fleet.rows()
    assert "cleo" not in summary["released"]


def test_given_a_recorded_rename_of_a_row_when_reconciled_then_the_row_is_held_and_said(fleet):
    _repo(fleet)
    _session(fleet, "cleo")
    record = reg.migration_record(f"{STEM}-cleo", f"{STEM}-dora", HOST, "claude-code")
    fleet.registry.transact(
        lambda s: reg.Change({reg.migration_path(f"{STEM}-cleo"): reg.encode_json(record)}, "intent (fixture)")
    )

    _code, planned = reconcile("-d")

    assert "cleo" not in planned["released"]
    assert "has not completed" in planned["held"]["cleo"]


def test_given_another_machines_row_when_released_by_hand_then_it_refuses_naming_the_holder(fleet):
    _repo(fleet)
    _session(fleet, "cleo", machine=OTHER_HOST)

    result = CliRunner().invoke(_cli_group, ["release-name", "cleo"])

    assert result.exit_code == 2
    assert f"only {OTHER_HOST} can test" in result.output
    assert "cleo" in fleet.rows()


def test_given_a_row_without_a_worktree_when_its_transcript_is_gone_then_it_is_released(fleet):
    repo = _repo(fleet)
    _session(fleet, "cleo", worktree=False)
    project = _add_transcript(repo, f"{STEM}-cleo", cwd=repo)

    reconcile()
    assert "cleo" in fleet.rows()
    (project / "s.jsonl").unlink()
    reconcile()

    assert "cleo" not in fleet.rows()


@pytest.mark.parametrize("present", ["worktree", "transcript"])
def test_given_a_present_artifact_when_released_by_hand_then_it_refuses_naming_the_test(fleet, present):
    repo = _repo(fleet)
    _session(fleet, "cleo")
    if present == "worktree":
        _add_worktree(repo, f"{STEM}-cleo")
    else:
        _add_transcript(repo, f"{STEM}-cleo", cwd=repo / ".worktrees" / f"{STEM}-cleo")

    result = CliRunner().invoke(_cli_group, ["release-name", "cleo"])

    assert result.exit_code == 2
    assert f"{present} present" in result.output
    assert "cleo" in fleet.rows()


def test_given_the_release_command_when_its_options_are_read_then_none_bypasses_the_probes():
    from ai_cli.name_commands import release_name_command

    assert [param.name for param in release_name_command.params] == ["name"]


def test_given_both_artifacts_gone_when_released_by_hand_then_the_row_is_deleted(fleet):
    _repo(fleet)
    _session(fleet, "cleo")

    result = CliRunner().invoke(_cli_group, ["release-name", "cleo"])

    assert result.exit_code == 0, result.output
    assert "cleo" not in fleet.rows()


def test_given_a_reassignment_when_it_succeeds_then_the_row_stays_held_with_only_its_owner_changed(fleet, monkeypatch):
    _repo(fleet)
    before = _session(fleet, "cleo")
    monkeypatch.setattr("ai_cli.name_commands._known_hosts", lambda: frozenset({HOST, OTHER_HOST}))

    result = CliRunner().invoke(_cli_group, ["reassign-name", "cleo", "-t", OTHER_HOST, "-r", "host renamed"])

    assert result.exit_code == 0, result.output
    after = fleet.rows()["cleo"]
    assert {key for key in reg.ROW_FIELDS if before[key] != after[key]} == {"machine"}
    assert after["machine"] == OTHER_HOST
    assert "host renamed" in git(fleet.remote, "log", "-1", "--format=%s", "refs/heads/fleet-session-names/registry")
    reconcile()
    assert "cleo" in fleet.rows(), "a reassigned row is not this host's to release"


def test_given_an_unknown_host_id_when_reassigning_then_it_is_refused(fleet, monkeypatch):
    _session(fleet, "cleo")
    monkeypatch.setattr("ai_cli.name_commands._known_hosts", lambda: frozenset({HOST}))

    result = CliRunner().invoke(_cli_group, ["reassign-name", "cleo", "-t", "nowhere", "-r", "x"])

    assert result.exit_code == 2
    assert fleet.rows()["cleo"]["machine"] == HOST


# --- T-3.1: spares after the limits narrow --------------------------------------------------------


def _narrow(fleet, high: int = 3) -> None:
    policy = policy_of(fleet.repo)
    policy["limits"]["max_name_length"] = high
    publish_list(fleet.registry, ["ada", "bob", "cyd", "eve", "ivy"], policy, policy_commit(fleet.repo))


def _spares(fleet, machine: str = HOST) -> list[str]:
    return sorted(token for token, row in fleet.rows().items() if row["kind"] == "spare" and row["machine"] == machine)


def test_given_the_limits_narrowed_when_reconciled_then_only_this_hosts_unactivated_out_of_limit_spares_go(fleet):
    _repo(fleet)
    reg.reserve_hand_chosen(  # the other machine's spare is a 4-letter name too
        fleet.other_machine(),
        "dora",
        reg.Slot(repo=REPO, repo_alias=REPO, host_alias="two", label=None, machine=OTHER_HOST, client="codex"),
        add=False,
        via="add-name",
        added_by="o@two",
        words=frozenset(),
    )
    other = fleet.other_machine("other-spares")
    other.transact(
        lambda s: reg.Change(
            {reg.row_path("kurt"): reg.checked_row(reg.spare_row(name="kurt", host_alias="two", machine=OTHER_HOST))},
            "spare (fixture)",
        )
    )
    fleet.registry.transact(
        lambda s: reg.Change(
            {reg.row_path("lola"): reg.checked_row(reg.spare_row(name="lola", host_alias="box", machine=HOST))},
            "spare (fixture)",
        )
    )
    session_row = _session(fleet, "milo")
    _add_transcript(fleet.projects / REPO, f"{STEM}-milo", cwd=fleet.projects / REPO / ".worktrees" / f"{STEM}-milo")
    _narrow(fleet)

    code, summary = reconcile()

    assert code == 0, summary
    assert summary["spares_released"] == ["lola"]
    rows = fleet.rows()
    assert "kurt" in rows and rows["kurt"]["machine"] == OTHER_HOST
    assert rows["milo"] == session_row
    assert all(len(token) <= 3 for token in _spares(fleet))
    assert len(_spares(fleet)) == 1  # topped back up to offline_spares


def test_given_a_spare_activated_offline_before_the_limits_narrowed_when_reconciled_then_it_is_a_session_reported_over_limit(
    fleet,
):
    _repo(fleet)
    fleet.registry.transact(
        lambda s: reg.Change(
            {reg.row_path("lola"): reg.checked_row(reg.spare_row(name="lola", host_alias="box", machine=HOST))},
            "spare (fixture)",
        )
    )
    fleet.registry.snapshot()
    fleet.offline()
    taken = reg.take_spare(
        fleet.registry,
        reg.Slot(repo=REPO, repo_alias=REPO, host_alias="box", label=None, machine=HOST, client="claude-code"),
    )
    fleet.online()
    # The session the spare backs is real: it has a transcript.
    _add_transcript(fleet.projects / REPO, taken.title, cwd=fleet.projects / REPO / ".worktrees" / taken.title)
    _narrow(fleet)

    reconcile()
    check = CliRunner().invoke(_cli_group, ["check-names"])

    assert fleet.rows()[taken.name]["kind"] == "session"
    assert check.exit_code == 1
    assert f"{taken.name} (4 letters)" in check.output


_RELEASE_RACE_CHILD = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path
    from ai_cli import name_registry as reg
    repo = Path(sys.argv[1])
    registry = reg.GitRegistry(repo, ref="refs/heads/fleet-session-names/registry", remote="origin",
                               policy_file=repo / reg.POLICY_MARKER, timeout_s=10, attempts=3)
    slot = reg.Slot(repo="myrepo", repo_alias="myrepo", host_alias="box", label=None, machine=sys.argv[2],
                    client="claude-code")
    try:
        print(json.dumps({"took": reg.take_spare(registry, slot).name}))
    except reg.SparesExhaustedError as exc:
        print(json.dumps({"refused": str(exc)}))
    """
)


def test_given_a_spare_release_holding_the_lock_when_an_offline_launch_wants_a_spare_then_it_never_takes_the_released_one(
    fleet, monkeypatch
):
    import os

    _repo(fleet)
    fleet.registry.transact(
        lambda s: reg.Change(
            {reg.row_path("lola"): reg.checked_row(reg.spare_row(name="lola", host_alias="box", machine=HOST))},
            "spare (fixture)",
        )
    )
    _narrow(fleet)
    real_push = reg.GitRegistry._push
    seen: dict = {}

    def push(self, commit):
        if self.repo == fleet.repo and "child" not in seen:
            # Between the release's final spool check and its push: an offline launch starts,
            # and another machine lands a write first so the push is refused and retried.
            seen["child"] = subprocess.Popen(
                [sys.executable, "-c", _RELEASE_RACE_CHILD, str(fleet.repo), HOST],
                stdout=subprocess.PIPE,
                text=True,
                env=dict(os.environ),
            )
            time.sleep(1.0)
            seen["waiting"] = seen["child"].poll() is None
            other = fleet.other_machine("racer")
            other.transact(lambda s: reg.Change({"README.md": b"racer\n"}, "unrelated (fixture)"))
        return real_push(self, commit)

    monkeypatch.setattr(reg.GitRegistry, "_push", push)

    code, summary = reconcile()
    outcome = json.loads(seen["child"].communicate(timeout=60)[0])

    assert code == 0, summary
    assert summary["spares_released"] == ["lola"]
    assert seen["waiting"], "the offline launch did not wait for the release's lock"
    assert outcome.get("took") != "lola"
    assert "lola" not in reg.read_spool()


# --- T-3.3: release against the writes that can land first -------------------------------------


def _land_before_push(monkeypatch, fleet, conflicting):
    real_push = reg.GitRegistry._push
    outcomes: list[bool] = []

    def push(self, commit):
        if self.repo == fleet.repo and not outcomes:
            conflicting()
        result = real_push(self, commit)
        if self.repo == fleet.repo:
            outcomes.append(result)
        return result

    monkeypatch.setattr(reg.GitRegistry, "_push", push)
    return outcomes


def test_given_a_reassignment_landing_first_when_a_release_retries_then_the_row_is_kept(fleet, monkeypatch):
    _repo(fleet)
    _session(fleet, "cleo")
    other = fleet.other_machine("reassigner")
    _land_before_push(
        monkeypatch,
        fleet,
        lambda: reg.reassign_name(other, "cleo", OTHER_HOST, "move", known_hosts=frozenset({OTHER_HOST})),
    )

    reconcile()

    assert fleet.rows()["cleo"]["machine"] == OTHER_HOST


def test_given_a_re_reservation_landing_first_when_a_release_retries_then_the_new_row_is_kept(fleet, monkeypatch):
    _repo(fleet)
    _session(fleet, "cleo")
    other = fleet.other_machine("rereserver")

    def release_and_take_again():
        other.transact(lambda s: reg.Change({reg.row_path("cleo"): None}, "release (fixture)"))
        reg.reserve_hand_chosen(
            other,
            "cleo",
            reg.Slot(repo=REPO, repo_alias=REPO, host_alias="two", label=None, machine=OTHER_HOST, client="codex"),
            add=False,
            via="add-name",
            added_by="o@two",
            words=frozenset(),
        )

    _land_before_push(monkeypatch, fleet, release_and_take_again)

    reconcile()

    assert fleet.rows()["cleo"]["machine"] == OTHER_HOST


def test_given_this_hosts_row_rewritten_first_when_a_release_retries_then_the_rewritten_row_is_kept(fleet, monkeypatch):
    _repo(fleet)
    _session(fleet, "cleo")
    other = fleet.other_machine("rewriter")

    def rewrite_on_this_host():
        other.transact(
            lambda s: reg.Change(
                {reg.row_path("cleo"): reg.checked_row({**s.rows["cleo"], "client": "codex"})}, "rewrite (fixture)"
            )
        )

    _land_before_push(monkeypatch, fleet, rewrite_on_this_host)

    reconcile()

    assert fleet.rows()["cleo"]["client"] == "codex"


def test_given_a_reassignment_landing_first_when_the_spool_is_published_then_the_entry_is_kept(fleet, monkeypatch):
    fleet.registry.transact(
        lambda s: reg.Change(
            {reg.row_path("lola"): reg.checked_row(reg.spare_row(name="lola", host_alias="box", machine=HOST))},
            "spare (fixture)",
        )
    )
    fleet.registry.snapshot()
    fleet.offline()
    reg.take_spare(
        fleet.registry,
        reg.Slot(repo=REPO, repo_alias=REPO, host_alias="box", label=None, machine=HOST, client="claude-code"),
    )
    fleet.online()
    other = fleet.other_machine("reassigner")
    _land_before_push(
        monkeypatch,
        fleet,
        lambda: reg.reassign_name(other, "lola", OTHER_HOST, "move", known_hosts=frozenset({OTHER_HOST})),
    )

    with reg.spool_lock(5), pytest.raises(reg.WriteRefusedError):
        reg.publish_spool(fleet.registry, HOST)

    assert "lola" in reg.read_spool()
    assert fleet.rows()["lola"]["kind"] == "spare"


def test_given_a_migration_intent_landing_first_when_a_release_retries_then_the_renamed_row_is_kept(fleet, monkeypatch):
    _repo(fleet)
    _session(fleet, "cleo")
    other = fleet.registry  # the same machine, in another process, starts renaming cleo's session

    def intent():
        record = reg.migration_record(f"{STEM}-cleo", f"{STEM}-dora", HOST, "claude-code")
        fleet.other_machine("migrator").transact(
            lambda s: reg.Change({reg.migration_path(f"{STEM}-cleo"): reg.encode_json(record)}, "intent (fixture)")
        )

    _land_before_push(monkeypatch, fleet, intent)
    del other

    reconcile()

    assert "cleo" in fleet.rows()


# --- T-3.9: the over-limit check ------------------------------------------------------------------


def _row(fleet, token: str, *, kind: str = "session", machine: str = HOST, state: str = "active") -> None:
    if kind == "spare":
        row = reg.spare_row(name=token, host_alias="box", machine=machine)
    elif kind == "label":
        row = reg.label_row(
            token=token,
            title=f"{STEM}-{token}",
            repo=REPO,
            repo_alias=REPO,
            host_alias="box",
            label=token,
            machine=machine,
            client="pi",
            worktree=None,
        )
    else:
        row = reg.session_row(
            name=token,
            title=f"{STEM}-{token}",
            repo=REPO,
            repo_alias=REPO,
            host_alias="box",
            label=None,
            machine=machine,
            client="pi",
            source="custom",
            tier=None,
            worktree=None,
        )
        if state != "active":
            row.update(state=state, recovery=[{"kind": "worktree", "identifier": f".worktrees/{STEM}-{token}"}])
    fleet.registry.transact(lambda s: reg.Change({reg.row_path(token): reg.checked_row(row)}, "row (fixture)"))


def check(*args):
    return CliRunner().invoke(_cli_group, ["check-names", *args])


def test_given_every_session_name_inside_the_limits_when_checked_then_it_exits_zero_with_counts_per_host(fleet):
    _row(fleet, "cleo")
    _row(fleet, "dora", machine=OTHER_HOST)

    result = check()

    assert result.exit_code == 0, result.output
    assert f"{HOST} 1" in result.output and f"{OTHER_HOST} 1" in result.output


def test_given_a_session_name_over_the_limit_when_checked_then_it_exits_one_with_a_line_per_row_grouped_by_host(fleet):
    _row(fleet, "gloria", machine=OTHER_HOST)
    _row(fleet, "rebecca")
    _row(fleet, "teamwork", kind="label")

    result = check()

    assert result.exit_code == 1
    assert f"{OTHER_HOST}:" in result.output and f"{HOST}:" in result.output
    assert "gloria (6 letters)" in result.output and f"{STEM}-gloria" in result.output
    assert result.output.count("ai session-migrate --all --to-names --over-limit -y") == 2
    assert "teamwork (8 letters)" not in result.output


def test_given_only_a_label_row_over_the_limit_when_checked_then_it_passes(fleet):
    _row(fleet, "teamwork", kind="label")

    assert check().exit_code == 0


def test_given_a_spare_over_the_limit_when_checked_then_it_exits_one_naming_reconcile_on_its_host(fleet):
    _row(fleet, "gloria", kind="spare", machine=OTHER_HOST)

    result = check()

    assert result.exit_code == 1
    assert "spare gloria" in result.output
    assert f"run on {OTHER_HOST}: ai reconcile-names" in result.output


def test_given_a_name_shorter_than_the_minimum_when_checked_then_it_is_grandfathered_and_passes(fleet):
    _row(fleet, "al")

    result = check()

    assert result.exit_code == 0
    assert "grandfathered: al" in result.output


def test_given_a_migration_incomplete_row_when_checked_then_it_is_listed_with_its_intent_and_survivors(fleet):
    _row(fleet, "dora", state="migration-incomplete")
    record = reg.migration_record(f"{STEM}-3", f"{STEM}-dora", HOST, "pi")
    stale = {
        **reg.migration_record(f"{STEM}-4", f"{STEM}-kurt", OTHER_HOST, "pi"),
        "started_at": "2000-01-01T00:00:00Z",
    }
    fleet.registry.transact(
        lambda s: reg.Change(
            {
                reg.migration_path(f"{STEM}-3"): reg.encode_json(record),
                reg.migration_path(f"{STEM}-4"): reg.encode_json(stale),
            },
            "intents (fixture)",
        )
    )

    result = check()

    assert result.exit_code == 0
    assert f"migration-incomplete: {STEM}-dora@{HOST} (rename of {STEM}-3)" in result.output
    assert f".worktrees/{STEM}-dora" in result.output
    assert f"unfinished rename: {STEM}-4 -> {STEM}-kurt on {OTHER_HOST}" in result.output


def test_given_the_registry_unreadable_when_checked_then_it_exits_three_and_prints_no_pass(fleet):
    fleet.offline()

    result = check()

    assert result.exit_code == 3
    assert "could not read the registry" in result.output
    assert "limits" not in result.output
