"""The session-name registry client (T-3.3) and the commands that write its name database (T-3.1).

Every test runs against a real bare git remote under ``tmp_path``: one checkout of an
environment repository that ships ``config/session-names.toml``, and, where two
machines are needed, a second clone of it. The remote ref is the registry; nothing
here reaches the network or a real remote.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from ai_cli import config as _config
from ai_cli import name_list, session_naming
from ai_cli import name_registry as reg
from ai_cli.main import _cli_group

REF = "refs/heads/fleet-session-names/registry"
HOST = "box-one"
OTHER_HOST = "box-two"
SOURCE_URL = "https://example.com/names.tar.gz"
WORDS_URL = "https://example.com/words.tgz"
FOUR_LETTER = ["bert", "cleo", "dora", "hugo", "iris", "jude", "kurt", "lola", "milo", "nora", "opal", "pete"]


def git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


POLICY_TOML = textwrap.dedent(
    f"""\
    schema = 1
    registry_ref = "{REF}"
    registry_remote = "origin"
    publish_timeout_s = 15
    publish_attempts = 5
    pick_floor = 10
    low_water = 25
    offline_spares = 3
    denylist = ["zed"]

    [limits]
    min_name_length = 3
    max_name_length = 4

    [source]
    url = "{SOURCE_URL}"
    digest = "sha256:{"0" * 64}"
    members = ["names/dist.male.first"]

    [confusable]
    words_url = "{WORDS_URL}"
    words_digest = "sha512-AAAA"
    words_member = "package/index.dic"
    max_edit_distance = 1
    extra_words = []
    keep_names = []

    [defaults]
    name_with_label = "keep"

    [hosts.{HOST}]
    enabled = true
    """
)


def _clone(remote: Path, target: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(target)], check=True, capture_output=True)
    git(target, "config", "user.email", "test@example.com")
    git(target, "config", "user.name", "Test")
    return target


def _registry(repo: Path) -> reg.GitRegistry:
    return reg.GitRegistry(
        repo, ref=REF, remote="origin", policy_file=repo / reg.POLICY_MARKER, timeout_s=15, attempts=5
    )


def _publish_list(registry: reg.Registry, names: list[str], policy: dict, policy_commit: str | None) -> None:
    record = {
        "schema": 1,
        "policy": policy,
        "policy_digest": name_list.policy_digest(policy),
        "policy_commit": policy_commit,
        "count": len(names),
    }
    registry.transact(
        lambda snapshot: reg.Change(
            {
                reg.NAMES_TXT: name_list.render_names(sorted(names)).encode(),
                reg.GENERATION_JSON: name_list.render_record(record).encode(),
            },
            "session-names: generate (fixture)",
        )
    )


def _policy(repo: Path) -> dict:
    return name_list.fleet_policy(reg.read_policy_file(repo / reg.POLICY_MARKER))


def _policy_commit(repo: Path) -> str:
    return git(repo, "log", "-1", "--format=%H", "--", "config/session-names.toml")


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    """A projects directory holding one environment checkout whose remote carries the registry."""
    projects = tmp_path / "projects"
    projects.mkdir()
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "user.name", "Test")
    (seed / "config").mkdir()
    (seed / "config" / "session-names.toml").write_text(POLICY_TOML)
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "policy")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "origin", "main")
    repo = _clone(remote, projects / "envrepo")
    monkeypatch.setenv("PROJECTS_DIR", str(projects))
    monkeypatch.setattr(_config, "_get_projects_dir", lambda: projects)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setattr(session_naming, "host_id", lambda: HOST)
    monkeypatch.setattr(reg, "alias_words", lambda: frozenset())
    registry = _registry(repo)
    _publish_list(registry, ["ada", *FOUR_LETTER], _policy(repo), _policy_commit(repo))
    return SimpleNamespace(tmp=tmp_path, projects=projects, remote=remote, seed=seed, repo=repo, registry=registry)


def _slot(machine: str = HOST, label: str | None = None, client: str = "claude-code") -> reg.Slot:
    return reg.Slot(repo="myrepo", repo_alias="myrepo", host_alias="box", label=label, machine=machine, client=client)


def _remote_rows(fleet) -> dict[str, dict]:
    listing = git(fleet.remote, "ls-tree", "--name-only", f"{REF}:names", check=False)
    return {
        Path(path).stem: json.loads(git(fleet.remote, "show", f"{REF}:names/{path}"))
        for path in listing.splitlines()
        if path
    }


def _remote_file(fleet, path: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(fleet.remote), "show", f"{REF}:{path}"], capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else None


def _tip(fleet) -> str:
    return git(fleet.remote, "rev-parse", REF)


def _changed_paths(fleet, commit: str) -> set[str]:
    return set(git(fleet.remote, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines())


# --- T-3.3: the write protocol -------------------------------------------------------------


def test_given_two_writers_from_one_tip_when_both_reserve_the_same_name_then_one_wins_and_the_other_picks_again(
    fleet, monkeypatch
):
    other = _registry(_clone(fleet.remote, fleet.tmp / "other-machine"))
    loser = fleet.registry
    real_push = reg.GitRegistry._push
    pushes: list[bool] = []

    def push_after_the_other_machine(self, commit):
        if self is loser and not pushes:
            # The other machine fetched the same tip and pushes first.
            winner.append(reg.reserve_picked(other, _slot(OTHER_HOST), words=frozenset(), rng=random.Random(1)))
        outcome = real_push(self, commit)
        if self is loser:
            pushes.append(outcome)
        return outcome

    winner: list[reg.Reservation] = []
    monkeypatch.setattr(reg.GitRegistry, "_push", push_after_the_other_machine)

    reservation = reg.reserve_picked(loser, _slot(), words=frozenset(), rng=random.Random(1))

    assert winner[0].name == "ada"  # the only 3-letter name, so both writers picked it first
    assert pushes == [False, True]
    assert reservation.name != "ada"
    rows = _remote_rows(fleet)
    assert rows["ada"]["machine"] == OTHER_HOST
    assert rows[reservation.name]["machine"] == HOST


def test_given_every_push_rejected_when_reserving_then_it_retries_exactly_publish_attempts_times(fleet, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(reg.GitRegistry, "_push", lambda self, commit: calls.append(commit) or False)
    before = _tip(fleet)

    with pytest.raises(reg.RegistryContendedError):
        reg.reserve_picked(fleet.registry, _slot(), words=frozenset())

    assert len(calls) == fleet.registry.attempts == 5
    assert _tip(fleet) == before


def test_given_a_reserve_when_it_publishes_then_nothing_is_forced_and_the_checkout_is_untouched(fleet, monkeypatch):
    status_before = git(fleet.repo, "status", "--porcelain")
    head_before = git(fleet.repo, "rev-parse", "HEAD")
    index_before = (fleet.repo / ".git" / "index").read_bytes()
    commands: list[list[str]] = []
    real_run = subprocess.run

    def recording_run(command, *args, **kwargs):
        if command[:1] == ["git"]:
            commands.append(list(command))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(reg.subprocess, "run", recording_run)

    reg.reserve_picked(fleet.registry, _slot(), words=frozenset())

    pushes = [command for command in commands if "push" in command]
    assert pushes
    for command in pushes:
        assert not {"-f", "--force", "--force-with-lease", "--mirror"} & set(command)
        assert not command[-1].startswith("+")
    assert git(fleet.repo, "status", "--porcelain") == status_before
    assert git(fleet.repo, "rev-parse", "HEAD") == head_before
    assert (fleet.repo / ".git" / "index").read_bytes() == index_before


def test_given_a_successful_reserve_when_published_then_the_same_commit_tops_this_machines_spares_up(fleet):
    reservation = reg.reserve_picked(fleet.registry, _slot(), words=frozenset())

    commit = _tip(fleet)
    spares = [row for row in _remote_rows(fleet).values() if row["kind"] == "spare" and row["machine"] == HOST]
    assert len(spares) == 3
    assert _changed_paths(fleet, commit) == {
        f"names/{reservation.name}.json",
        *(f"names/{s['token']}.json" for s in spares),
    }
    assert set(reservation.spares_added) == {row["token"] for row in spares}


def test_given_spares_already_held_when_reserving_again_then_only_the_shortfall_is_added(fleet):
    reg.reserve_picked(fleet.registry, _slot(), words=frozenset())

    second = reg.reserve_picked(fleet.registry, _slot(), words=frozenset())

    assert second.spares_added == []
    assert _changed_paths(fleet, _tip(fleet)) == {f"names/{second.name}.json"}


def _go_offline(fleet) -> None:
    git(fleet.repo, "remote", "set-url", "origin", str(fleet.tmp / "missing.git"))


def _go_online(fleet) -> None:
    git(fleet.repo, "remote", "set-url", "origin", str(fleet.remote))


def test_given_an_unreachable_registry_when_launching_then_a_spare_is_activated_in_the_spool(fleet):
    reg.reserve_picked(fleet.registry, _slot(), words=frozenset())
    spares = sorted(row["token"] for row in _remote_rows(fleet).values() if row["kind"] == "spare")
    _go_offline(fleet)

    with pytest.raises(reg.RegistryUnreachableError):
        reg.reserve_picked(fleet.registry, _slot(), words=frozenset())
    taken = reg.take_spare(fleet.registry, _slot(label="probe"))

    assert taken.offline
    assert taken.name == min(spares, key=lambda token: (len(token), token))
    assert taken.title == f"myrepo-box-probe-{taken.name}"
    spool = reg.read_spool()
    assert set(spool) == {taken.name}
    assert spool[taken.name]["kind"] == "session"
    assert spool[taken.name]["op"] == "activate"


def test_given_no_spare_left_when_offline_then_taking_one_refuses_naming_the_reconcile_command(fleet):
    reg.reserve_picked(fleet.registry, _slot(), words=frozenset())
    _go_offline(fleet)
    for _ in range(3):
        reg.take_spare(fleet.registry, _slot())

    with pytest.raises(reg.SparesExhaustedError) as caught:
        reg.take_spare(fleet.registry, _slot())

    assert "ai reconcile-names" in str(caught.value)
    assert len(reg.read_spool()) == 3


def test_given_a_spooled_activation_when_the_next_command_runs_online_then_it_is_published_and_the_spool_empties(
    fleet,
):
    reg.reserve_picked(fleet.registry, _slot(), words=frozenset())
    _go_offline(fleet)
    taken = reg.take_spare(fleet.registry, _slot(label="probe"))
    _go_online(fleet)

    result = CliRunner().invoke(_cli_group, ["sync-names", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert reg.read_spool() == {}
    row = _remote_rows(fleet)[taken.name]
    assert row["kind"] == "session"
    assert row["title"] == f"myrepo-box-probe-{taken.name}"
    assert row["source"] == "spare"


def test_given_a_spare_activation_when_published_then_only_the_activation_fields_change(fleet):
    reg.reserve_picked(fleet.registry, _slot(), words=frozenset())
    spares = {token: row for token, row in _remote_rows(fleet).items() if row["kind"] == "spare"}
    _go_offline(fleet)
    taken = reg.take_spare(fleet.registry, _slot(label="probe"))
    _go_online(fleet)

    with reg.spool_lock(5):
        reg.publish_spool(fleet.registry, HOST)

    before, after = spares[taken.name], _remote_rows(fleet)[taken.name]
    changed = {key for key in reg.ROW_FIELDS if before[key] != after[key]}
    assert changed <= reg.SPARE_ACTIVATION_FIELDS
    assert {"kind", "title", "activated_at"} <= changed
    assert after["token"] == before["token"]
    assert after["reserved_at"] == before["reserved_at"]
    assert after["machine"] == before["machine"]


def test_given_a_new_hand_chosen_name_when_reserved_with_add_then_one_commit_adds_it_and_holds_it(fleet):
    reservation = reg.reserve_hand_chosen(
        fleet.registry, "zara", _slot(), add=True, via="launch-prompt", added_by="t@box", words=frozenset()
    )

    assert reservation.added_to_database
    assert _changed_paths(fleet, _tip(fleet)) == {"available/manual/zara.json", "names/zara.json"}
    assert json.loads(_remote_file(fleet, "available/manual/zara.json"))["via"] == "launch-prompt"
    assert _remote_rows(fleet)["zara"]["source"] == "manual"


def test_given_a_new_hand_chosen_name_without_add_when_reserved_then_it_is_refused_as_unknown(fleet):
    before = _tip(fleet)

    with pytest.raises(reg.HandChosenRefusedError) as caught:
        reg.reserve_hand_chosen(
            fleet.registry, "zara", _slot(), add=False, via="launch-prompt", added_by="t@box", words=frozenset()
        )

    assert caught.value.verdict == "unknown"
    assert _tip(fleet) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [("reserved_at", "2001-01-01T00:00:00Z"), ("source", "picked"), ("host_alias", "elsewhere")],
)
def test_given_a_spool_entry_changing_more_than_the_activation_when_published_then_it_is_refused_and_kept(
    fleet, field, value
):
    reg.reserve_picked(fleet.registry, _slot(), words=frozenset())
    _go_offline(fleet)
    taken = reg.take_spare(fleet.registry, _slot())
    entry_path = reg.spool_dir() / f"{taken.name}.json"
    entry = json.loads(entry_path.read_text())
    entry[field] = value
    entry_path.write_text(json.dumps(entry))
    _go_online(fleet)
    before = _remote_rows(fleet)[taken.name]

    with reg.spool_lock(5), pytest.raises(reg.WriteRefusedError):
        reg.publish_spool(fleet.registry, HOST)

    assert _remote_rows(fleet)[taken.name] == before
    assert entry_path.is_file()


_TAKE_SPARE_CHILD = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path
    from ai_cli import name_registry as reg
    repo = Path(sys.argv[1])
    registry = reg.GitRegistry(repo, ref=sys.argv[2], remote="origin", policy_file=repo / reg.POLICY_MARKER,
                               timeout_s=5, attempts=2)
    slot = reg.Slot(repo="myrepo", repo_alias="myrepo", host_alias="box", label=None, machine=sys.argv[3],
                    client="claude-code")
    try:
        taken = reg.take_spare(registry, slot)
    except reg.SparesExhaustedError as exc:
        print(json.dumps({"refused": str(exc)}))
    else:
        print(json.dumps({"took": taken.name}))
    """
)


def test_given_two_offline_launches_and_one_spare_when_both_run_then_exactly_one_takes_it(fleet, monkeypatch):
    reg.reserve_picked(fleet.registry, _slot(), words=frozenset())
    _go_offline(fleet)
    reg.take_spare(fleet.registry, _slot())
    reg.take_spare(fleet.registry, _slot())
    assert len(reg.read_spool()) == 2
    import os

    env = dict(os.environ)
    children = [
        subprocess.Popen(
            [sys.executable, "-c", _TAKE_SPARE_CHILD, str(fleet.repo), REF, HOST],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for _ in range(2)
    ]
    outcomes = [json.loads(child.communicate(timeout=60)[0]) for child in children]

    assert sorted("took" in outcome for outcome in outcomes) == [False, True]
    refused = next(outcome for outcome in outcomes if "refused" in outcome)
    assert "no unactivated spare" in refused["refused"]
    assert len(reg.read_spool()) == 3


def test_given_rows_written_by_every_path_when_read_then_each_holds_its_kind_invariants_and_no_absolute_path(fleet):
    reg.reserve_picked(fleet.registry, _slot(), words=frozenset())
    reg.reserve_hand_chosen(
        fleet.registry, "zara", _slot(label="probe"), add=True, via="launch-prompt", added_by="t@box", words=frozenset()
    )
    _go_offline(fleet)
    reg.take_spare(fleet.registry, _slot())
    _go_online(fleet)
    with reg.spool_lock(5):
        reg.publish_spool(fleet.registry, HOST)

    rows = _remote_rows(fleet)

    assert {row["kind"] for row in rows.values()} == {"session", "spare"}
    for token, row in rows.items():
        assert reg.row_problem(row) is None, token
        assert not any(isinstance(value, str) and value.startswith(("/", "~")) for value in row.values()), token


@pytest.mark.parametrize(
    ("kind", "field", "value"),
    [
        ("session", "title", None),
        ("session", "worktree", "/home/user/projects/myrepo/.worktrees/myrepo-box-ada"),
        ("spare", "title", "myrepo-box-ada"),
        ("spare", "source", "picked"),
        ("label", "name", "ada"),
    ],
)
def test_given_a_row_breaking_its_kind_invariants_when_written_then_it_is_refused(kind, field, value):
    rows = {
        "session": reg.session_row(
            name="ada",
            title="myrepo-box-ada",
            repo="myrepo",
            repo_alias="myrepo",
            host_alias="box",
            label=None,
            machine=HOST,
            client="claude-code",
            source="picked",
            tier="len3/0/other",
            worktree=".worktrees/myrepo-box-ada",
        ),
        "spare": reg.spare_row(name="ada", host_alias="box", machine=HOST),
        "label": reg.label_row(
            token="probe",
            title="myrepo-box-probe",
            repo="myrepo",
            repo_alias="myrepo",
            host_alias="box",
            label="probe",
            machine=HOST,
            client="claude-code",
            worktree=None,
        ),
    }
    row = rows[kind]
    assert reg.row_problem(row) is None
    row[field] = value

    with pytest.raises(reg.WriteRefusedError):
        reg.checked_row(row)


def test_given_no_sibling_registry_when_discovered_then_the_local_registry_is_used_under_a_lock(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    (projects / "plainrepo").mkdir(parents=True)
    monkeypatch.setenv("PROJECTS_DIR", str(projects))
    monkeypatch.setattr(_config, "_get_projects_dir", lambda: projects)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setattr(reg, "alias_words", lambda: frozenset())

    registry = reg.discover().registry

    assert isinstance(registry, reg.LocalRegistry)
    # The data home is $XDG_DATA_HOME/ai-cli-utils on POSIX and %LOCALAPPDATA%\ai-cli-utils on Windows.
    assert registry.root == _config.get_xdg_data_home() / "session-names"
    reservation = reg.reserve_picked(registry, _slot(), words=frozenset())
    assert (registry.root / "names" / f"{reservation.name}.json").is_file()
    assert reservation.name in name_list.read_names(name_list.DATA_DIR / name_list.NAMES_FILE)
    import portalocker

    registry.timeout_s, registry.attempts = 0.2, 1
    with (
        portalocker.Lock(str(registry.root / "registry.lock"), mode="a", timeout=1),
        pytest.raises(reg.RegistryContendedError),
    ):
        reg.reserve_picked(registry, _slot(), words=frozenset())


def test_given_a_project_registry_without_a_naming_policy_when_discovered_then_index_names_are_kept(
    tmp_path, monkeypatch
):
    projects = tmp_path / "projects"
    (projects / "envrepo" / "config").mkdir(parents=True)
    (projects / "envrepo" / "config" / "fleet-projects.toml").write_text("max_length = 6\n")
    monkeypatch.setenv("PROJECTS_DIR", str(projects))
    monkeypatch.setattr(_config, "_get_projects_dir", lambda: projects)

    discovery = reg.discover()
    result = CliRunner().invoke(_cli_group, ["sync-names"])

    assert discovery.registry is None
    assert "predates human session names" in discovery.reason
    assert result.exit_code == 1
    assert "predates human session names" in result.output


# --- T-3.1: the published policy governs every allocation ----------------------------------


def test_given_the_hosts_policy_file_changed_unpublished_when_picking_then_the_published_limits_govern(fleet):
    policy_file = fleet.repo / "config" / "session-names.toml"
    policy_file.write_text(policy_file.read_text().replace("max_name_length = 4", "max_name_length = 3"))
    git(fleet.repo, "commit", "-qam", "narrow locally, unpublished")
    names = []
    for _ in range(4):
        names.append(reg.reserve_picked(fleet.registry, _slot(), words=frozenset()).name)

    assert any(len(name) == 4 for name in names), "a local, unpublished max of 3 must have no effect"


@pytest.mark.parametrize(
    ("mutate", "unknown"),
    [
        (lambda policy: policy.update(schema=2), "schema 2"),
        (lambda policy: policy.update(new_rule=True), "new_rule"),
        (lambda policy: policy["limits"].update(max_name_width=4), "limits.max_name_width"),
    ],
)
def test_given_a_snapshot_this_version_does_not_know_when_allocating_then_it_refuses_naming_the_key(
    fleet, mutate, unknown
):
    policy = _policy(fleet.repo)
    mutate(policy)
    _publish_list(fleet.registry, ["ada", *FOUR_LETTER], policy, _policy_commit(fleet.repo))
    before = _tip(fleet)

    with pytest.raises(reg.PolicyUnknownError) as caught:
        reg.reserve_picked(fleet.registry, _slot(), words=frozenset())

    assert "update ai-cli-utils" in str(caught.value)
    assert unknown in str(caught.value)
    assert _tip(fleet) == before


def test_given_an_unknown_snapshot_when_adding_a_name_then_the_command_refuses(fleet, monkeypatch):
    policy = _policy(fleet.repo)
    policy["future"] = 1
    _publish_list(fleet.registry, ["ada", *FOUR_LETTER], policy, _policy_commit(fleet.repo))
    monkeypatch.setattr(reg, "common_words", lambda policy, **_: frozenset())

    result = CliRunner().invoke(_cli_group, ["add-name", "zara"])

    assert result.exit_code != 0
    assert "update ai-cli-utils" in result.output
    assert "future" in result.output


def _all_fleet_keys(policy: dict) -> list[tuple[str, ...]]:
    keys: list[tuple[str, ...]] = []
    for key, value in policy.items():
        if isinstance(value, dict):
            keys += [(key, inner) for inner in value]
        else:
            keys.append((key,))
    return keys


def _changed(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, list):
        return [*value, "x"]
    return f"{value}-changed"


@pytest.mark.parametrize("path", _all_fleet_keys(name_list.fleet_policy(__import__("tomllib").loads(POLICY_TOML))))
def test_given_one_fleet_wide_key_changed_when_digested_then_the_policy_digest_changes(path):
    import copy
    import tomllib

    config = tomllib.loads(POLICY_TOML)
    before = name_list.policy_digest(name_list.fleet_policy(config))
    changed = copy.deepcopy(config)
    table = changed
    for key in path[:-1]:
        table = table[key]
    table[path[-1]] = _changed(table[path[-1]])

    assert name_list.policy_digest(name_list.fleet_policy(changed)) != before


@pytest.mark.parametrize("table", ["hosts", "defaults"])
def test_given_a_per_machine_key_changed_when_digested_then_the_policy_digest_is_unchanged(table):
    import tomllib

    config = tomllib.loads(POLICY_TOML)
    before = name_list.policy_digest(name_list.fleet_policy(config))
    config[table]["extra"] = "value"
    config["hosts"]["another-host"] = {"enabled": False}

    assert name_list.policy_digest(name_list.fleet_policy(config)) == before


# --- T-3.1: sync-names and the four ancestry states ----------------------------------------


@pytest.fixture
def downloads(monkeypatch):
    """The two pinned artifacts the policy names, served from memory and counted."""
    import base64
    import hashlib
    import io
    import tarfile

    def tarball(member: str, text: str) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            data = text.encode()
            info = tarfile.TarInfo(member)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        return buffer.getvalue()

    source = tarball(
        "names/dist.male.first",
        "".join(f"{n.upper():<15}0.1 0.1 1\n" for n in ["ada", "bob", "eve", "ida", "otto", "anna", "zed", "art"]),
    )
    words = tarball("package/index.dic", "2\nart/S\nbob\n")
    served = {SOURCE_URL: source, WORDS_URL: words}
    fetched: list[str] = []

    def fetch(url):
        fetched.append(url)
        return served[url]

    monkeypatch.setattr(name_list, "download", fetch)
    digests = {
        "source": "sha256:" + hashlib.sha256(source).hexdigest(),
        "words": "sha512-" + base64.b64encode(hashlib.sha512(words).digest()).decode(),
    }
    return SimpleNamespace(fetched=fetched, digests=digests)


def _commit_policy(repo: Path, transform) -> str:
    path = repo / "config" / "session-names.toml"
    path.write_text(transform(path.read_text()))
    git(repo, "commit", "-qam", "policy change")
    return git(repo, "rev-parse", "HEAD")


def _pin_downloads(text: str, downloads) -> str:
    return text.replace(f'"sha256:{"0" * 64}"', f'"{downloads.digests["source"]}"').replace(
        '"sha512-AAAA"', f'"{downloads.digests["words"]}"'
    )


def test_given_the_same_policy_revision_when_syncing_then_nothing_is_downloaded_or_published(fleet, downloads):
    before = _tip(fleet)

    result = CliRunner().invoke(_cli_group, ["sync-names"])

    assert result.exit_code == 0, result.output
    assert "already reflects" in result.output
    assert downloads.fetched == []
    assert _tip(fleet) == before


def test_given_a_newer_local_policy_when_syncing_then_one_commit_publishes_only_the_list_and_its_record(
    fleet, downloads
):
    commit = _commit_policy(fleet.repo, lambda text: _pin_downloads(text, downloads))
    git(fleet.repo, "push", "-q", "origin", "HEAD:main")
    reg.reserve_picked(fleet.registry, _slot(), words=frozenset())
    reg.add_name(fleet.registry, "zara", added_by="t@box", reason=None, words_for=lambda policy: frozenset())
    before = _tip(fleet)

    result = CliRunner().invoke(_cli_group, ["sync-names"])

    assert result.exit_code == 0, result.output
    tip = _tip(fleet)
    assert git(fleet.remote, "rev-parse", f"{tip}^") == before
    assert _changed_paths(fleet, tip) == {reg.NAMES_TXT, reg.GENERATION_JSON}
    record = json.loads(_remote_file(fleet, reg.GENERATION_JSON))
    assert record["policy_commit"] == commit
    # bob and art are dictionary words, zed is denylisted.
    assert _remote_file(fleet, reg.NAMES_TXT).split() == ["ada", "anna", "eve", "ida", "otto"]
    assert sorted(downloads.fetched) == sorted([SOURCE_URL, WORDS_URL])


def test_given_a_newer_revision_with_the_same_digest_when_syncing_then_nothing_is_downloaded_or_written(
    fleet, downloads
):
    _commit_policy(fleet.repo, lambda text: text + "\n# a comment changes the commit, not the policy\n")
    git(fleet.repo, "push", "-q", "origin", "HEAD:main")
    before = _tip(fleet)

    result = CliRunner().invoke(_cli_group, ["sync-names"])

    assert result.exit_code == 0, result.output
    assert downloads.fetched == []
    assert _tip(fleet) == before


def test_given_an_older_local_policy_when_syncing_then_nothing_is_published_and_it_exits_zero(fleet, downloads):
    ahead = _clone(fleet.remote, fleet.tmp / "ahead")
    commit = _commit_policy(ahead, lambda text: _pin_downloads(text, downloads))
    git(ahead, "push", "-q", "origin", "HEAD:main")
    _publish_list(_registry(ahead), ["ada", "otto"], _policy(ahead), commit)
    before = _tip(fleet)

    result = CliRunner().invoke(_cli_group, ["sync-names"])

    assert result.exit_code == 0, result.output
    assert "older than the published one" in result.output
    assert downloads.fetched == []
    assert _tip(fleet) == before


def test_given_diverged_policy_histories_when_syncing_then_it_exits_non_zero_publishing_nothing(fleet, downloads):
    ahead = _clone(fleet.remote, fleet.tmp / "ahead")
    theirs = _commit_policy(ahead, lambda text: text.replace("pick_floor = 10", "pick_floor = 9"))
    git(ahead, "push", "-q", "origin", "HEAD:refs/heads/their-branch")
    _publish_list(_registry(ahead), ["ada", "otto"], _policy(ahead), theirs)
    _commit_policy(fleet.repo, lambda text: text.replace("pick_floor = 10", "pick_floor = 8"))
    git(fleet.repo, "fetch", "-q", "origin", "their-branch")
    before = _tip(fleet)

    result = CliRunner().invoke(_cli_group, ["sync-names"])

    assert result.exit_code == 1
    assert "cannot be ordered" in result.output
    assert downloads.fetched == []
    assert _tip(fleet) == before


def test_given_a_recorded_policy_commit_absent_after_the_fetch_when_syncing_then_it_exits_non_zero(fleet, downloads):
    _publish_list(fleet.registry, ["ada", "otto"], _policy(fleet.repo), "f" * 40)
    before = _tip(fleet)

    result = CliRunner().invoke(_cli_group, ["sync-names"])

    assert result.exit_code == 1
    assert "cannot be ordered" in result.output
    assert _tip(fleet) == before


def test_given_an_empty_registry_ref_when_syncing_then_it_counts_as_local_newer_and_publishes(fleet, downloads):
    git(fleet.remote, "update-ref", "-d", REF)
    _commit_policy(fleet.repo, lambda text: _pin_downloads(text, downloads))

    result = CliRunner().invoke(_cli_group, ["sync-names"])

    assert result.exit_code == 0, result.output
    assert _remote_file(fleet, reg.NAMES_TXT) is not None


def test_given_a_failed_digest_during_sync_when_run_then_it_fails_naming_the_url_and_the_ref_is_unchanged(
    fleet, downloads
):
    _commit_policy(fleet.repo, lambda text: text.replace("pick_floor = 10", "pick_floor = 11"))
    before = _tip(fleet)

    result = CliRunner().invoke(_cli_group, ["sync-names"])

    assert result.exit_code == 1
    assert SOURCE_URL in result.output
    assert _tip(fleet) == before


# --- T-3.1: add-name and remove-name ---------------------------------------------------------


@pytest.fixture
def no_words(monkeypatch):
    words = {"may", "art"}
    monkeypatch.setattr(reg, "common_words", lambda policy, **_: frozenset(words))
    return words


def test_given_a_new_in_limit_name_when_added_then_its_manual_record_is_published(fleet, no_words):
    result = CliRunner().invoke(_cli_group, ["add-name", "Zara", "-r", "requested by the team"])

    assert result.exit_code == 0, result.output
    record = json.loads(_remote_file(fleet, "available/manual/zara.json"))
    assert record["via"] == "add-name"
    assert record["reason"] == "requested by the team"
    assert record["added_by"].endswith(f"@{HOST}")


def test_given_a_name_over_the_limit_when_added_then_it_exits_two_printing_its_length_and_both_limits(fleet, no_words):
    before = _tip(fleet)

    result = CliRunner().invoke(_cli_group, ["add-name", "rebecca"])

    assert result.exit_code == 2
    assert "is 7 letters" in result.output
    assert "3 to 4 letters" in result.output
    assert _tip(fleet) == before


def test_given_a_common_word_when_added_then_it_exits_two_naming_keep_names(fleet, no_words):
    result = CliRunner().invoke(_cli_group, ["add-name", "may"])

    assert result.exit_code == 2
    assert "keep_names" in result.output


def test_given_a_name_already_in_the_database_when_added_then_it_exits_zero_and_says_so(fleet, no_words):
    before = _tip(fleet)

    result = CliRunner().invoke(_cli_group, ["add-name", "ada"])

    assert result.exit_code == 0
    assert "already in the name database" in result.output
    assert _tip(fleet) == before


def test_given_a_free_hand_added_name_when_removed_then_one_commit_deletes_only_its_record(fleet, no_words):
    CliRunner().invoke(_cli_group, ["add-name", "zara"])

    result = CliRunner().invoke(_cli_group, ["remove-name", "zara"])

    assert result.exit_code == 0, result.output
    assert _changed_paths(fleet, _tip(fleet)) == {"available/manual/zara.json"}
    assert _remote_file(fleet, "available/manual/zara.json") is None


def test_given_a_held_name_when_removed_then_it_exits_two_naming_the_holder(fleet, no_words):
    reg.reserve_hand_chosen(
        fleet.registry, "zara", _slot(), add=True, via="launch-prompt", added_by="t@box", words=frozenset()
    )

    result = CliRunner().invoke(_cli_group, ["remove-name", "zara"])

    assert result.exit_code == 2
    assert f"myrepo-box-zara@{HOST}" in result.output


def test_given_a_generated_name_when_removed_then_it_exits_two_saying_generated_names_change_through_config(
    fleet, no_words
):
    result = CliRunner().invoke(_cli_group, ["remove-name", "bert"])

    assert result.exit_code == 2
    assert "generated names change through" in result.output


# --- T-3.3: every write rechecks its guard on the new tip ---------------------------------


def _conflict_before_first_push(monkeypatch, registry: reg.GitRegistry, conflicting) -> list[bool]:
    """Run ``conflicting`` once, just before ``registry``'s first push, then let the push proceed."""
    real_push = reg.GitRegistry._push
    outcomes: list[bool] = []

    def push(self, commit):
        if self is registry and not outcomes:
            conflicting()
        outcome = real_push(self, commit)
        if self is registry:
            outcomes.append(outcome)
        return outcome

    monkeypatch.setattr(reg.GitRegistry, "_push", push)
    return outcomes


def test_given_add_name_landing_first_when_a_hand_chosen_reserve_retries_then_it_reserves_without_rewriting_the_record(
    fleet, monkeypatch
):
    other = _registry(_clone(fleet.remote, fleet.tmp / "other"))
    outcomes = _conflict_before_first_push(
        monkeypatch,
        fleet.registry,
        lambda: reg.add_name(other, "zara", added_by="other@box", reason="first", words_for=lambda p: frozenset()),
    )

    reservation = reg.reserve_hand_chosen(
        fleet.registry, "zara", _slot(), add=True, via="launch-prompt", added_by="t@box", words=frozenset()
    )

    assert outcomes == [False, True]
    assert reservation.added_to_database is False
    assert json.loads(_remote_file(fleet, "available/manual/zara.json"))["added_by"] == "other@box"
    assert _remote_rows(fleet)["zara"]["source"] == "custom"


def _narrow_to_three(other_repo: Path, names: list[str], extra_words: list[str] | None = None):
    def run():
        policy = _policy(other_repo)
        policy["limits"]["max_name_length"] = 3
        if extra_words:
            policy["confusable"]["extra_words"] = extra_words
        _publish_list(_registry(other_repo), names, policy, _policy_commit(other_repo))

    return run


def test_given_a_regeneration_narrowing_the_limits_when_a_reserve_retries_then_no_out_of_limit_name_is_written(
    fleet, monkeypatch
):
    reg.reserve_picked(fleet.registry, _slot(), words=frozenset())  # holds ada and the spares
    other = _clone(fleet.remote, fleet.tmp / "other")
    outcomes = _conflict_before_first_push(
        monkeypatch, fleet.registry, _narrow_to_three(other, ["ada", "bob", "cyd", "eve", "ivy", "kai", "zed"])
    )

    reservation = reg.reserve_picked(fleet.registry, _slot(), words=frozenset())

    assert outcomes == [False, True]
    assert len(reservation.name) == 3
    written = _changed_paths(fleet, _tip(fleet))
    assert all(len(Path(path).stem) == 3 for path in written), written


def test_given_a_regeneration_narrowing_the_limits_when_spares_top_up_then_every_new_spare_is_in_the_new_limits(
    fleet, monkeypatch
):
    other = _clone(fleet.remote, fleet.tmp / "other")
    _conflict_before_first_push(
        monkeypatch, fleet.registry, _narrow_to_three(other, ["ada", "bob", "cyd", "eve", "ivy", "kai", "lou", "zed"])
    )

    reservation = reg.reserve_picked(fleet.registry, _slot(), words=frozenset())

    assert reservation.spares_added
    assert all(len(name) == 3 for name in [reservation.name, *reservation.spares_added])


def test_given_a_regeneration_adding_a_common_word_when_add_name_retries_then_the_word_is_refused(fleet, monkeypatch):
    other = _clone(fleet.remote, fleet.tmp / "other")

    def regenerate_with_word():
        policy = _policy(other)
        policy["confusable"]["extra_words"] = ["zara"]
        _publish_list(_registry(other), ["ada", *FOUR_LETTER], policy, _policy_commit(other))

    outcomes = _conflict_before_first_push(monkeypatch, fleet.registry, regenerate_with_word)
    before_conflict = _tip(fleet)

    with pytest.raises(reg.HandChosenRefusedError) as caught:
        reg.add_name(fleet.registry, "zara", added_by="t@box", reason=None, words_for=lambda p: frozenset())

    assert outcomes == [False]
    assert caught.value.verdict == "word"
    assert _remote_file(fleet, "available/manual/zara.json") is None
    assert _tip(fleet) != before_conflict  # only the other machine's regeneration landed


def test_given_a_newer_regeneration_landing_first_when_a_sync_retries_then_it_publishes_nothing(
    fleet, downloads, monkeypatch
):
    commit = _commit_policy(fleet.repo, lambda text: _pin_downloads(text, downloads))
    git(fleet.repo, "push", "-q", "origin", "HEAD:main")
    ahead = _clone(fleet.remote, fleet.tmp / "ahead")
    newer = _commit_policy(ahead, lambda text: text.replace("pick_floor = 10", "pick_floor = 12"))
    git(ahead, "push", "-q", "origin", "HEAD:main")
    git(fleet.repo, "fetch", "-q", "origin")
    outcomes = _conflict_before_first_push(
        monkeypatch, fleet.registry, lambda: _publish_list(_registry(ahead), ["ada", "otto"], _policy(ahead), newer)
    )

    result = reg.sync_names(fleet.registry, local_policy=_policy(fleet.repo))

    assert outcomes == [False]
    assert result.state == "local-older"
    assert result.published is False
    assert json.loads(_remote_file(fleet, reg.GENERATION_JSON))["policy_commit"] == newer
    assert commit != newer
