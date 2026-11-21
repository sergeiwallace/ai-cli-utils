"""`ai sessions` and `ai sessions list` print a grouped, sorted table of this host's and other hosts' sessions."""

import json
import subprocess
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_session_registry import state_home as state_home

from ai_cli import session_registry as registry
from ai_cli import session_state_publish as publish
from ai_cli.main import _cli_group

REPO = Path(__file__).resolve().parents[1]
LOCAL = "host-a"


def _record(name, status, *, changed="2026-01-02T03:04:05Z", cause="", relaunch="auto"):
    record = {
        "id": name,
        "kind": "local",
        "name": name,
        "owning_host": LOCAL,
        "boot_id": "previous-boot",
        "relaunch": relaunch,
        "changed_at": changed,
        "launched_at": changed,
        "ended_at": None,
        "exit": None,
    }
    if status == "exited":
        record.update(run_state="exited", ended_at=changed, supervisor_exit={"cause": cause or "manual_exit"})
    elif status != "unknown":
        record["run_state"] = status
    return record


@pytest.fixture
def host(state_home, monkeypatch):
    """This host's registry and config.toml, with tmux reporting every session alive."""
    cfg = {"machine": {"host_id": LOCAL, "profile_class": "personal"}}
    monkeypatch.setattr("ai_cli.config.load_config", lambda **_kwargs: cfg)
    monkeypatch.setattr(registry, "_local_session_alive", lambda _name: True)

    def write(*records):
        path = registry.registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": 2, "machine": LOCAL, "sessions": list(records)}), encoding="utf-8")

    write(
        _record("c-myproject-eve", "exited", changed="2026-01-03T00:00:00Z"),
        _record("c-myproject-bob", "running", changed="2026-01-01T00:00:00Z"),
        _record("c-myproject-amy", "unknown"),
        _record("c-myproject-ada", "running", changed="2026-01-02T00:00:00Z", relaunch="never"),
    )
    return cfg, write


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def published(host, tmp_path):
    """A bare publication remote preloaded with snapshots, plus a fresh reader checkout."""
    cfg, _ = host
    remote, repo = tmp_path / "remote.git", tmp_path / "repo"
    remote.mkdir()
    repo.mkdir()
    _git(remote, "init", "--bare")
    _git(repo, "init")
    _git(remote, "config", "user.name", "Example User")
    _git(remote, "config", "user.email", "user@example.com")
    _git(repo, "remote", "add", "origin", str(remote))
    for other, sessions in {
        LOCAL: {"c-myproject-bob": "running"},
        "host-b": {"c-webapp-kim": "running", "c-webapp-lou": "exited"},
        "host-c": {"c-myapp-max": "running"},
        "host-d": {},
    }.items():
        lines = [f'host = "{other}"', 'boot_id = ""', 'published_at = "2026-01-04T00:00:00Z"']
        for name, run_state in sessions.items():
            lines += ["", f'[sessions."{name}"]', f'run_state = "{run_state}"', 'cause = ""']
            lines += ['changed_at = "2026-01-04T00:00:00Z"', 'relaunch = "auto"']
        publisher = publish.Publisher(
            {
                "repo": str(remote),
                "remote": "origin",
                "ref_prefix": "refs/heads/fleet-sessions/",
                "host": other,
                "debounce_seconds": 600,
            }
        )
        blob = publisher.git("hash-object", "-w", "--stdin", stdin="\n".join(lines) + "\n")
        tree = publisher.tree({f"sessions/{other}.toml": blob})
        commit = publisher.git("commit-tree", tree, "-m", "Publish test snapshot")
        publisher.git("update-ref", publisher.ref, commit)
    cfg["session_relaunch"] = {"publish": {"repo": str(repo)}}
    return cfg


def _run(*args):
    result = CliRunner().invoke(_cli_group, list(args), terminal_width=200)
    assert result.exit_code == 0, result.output
    return result.output


def _table(text):
    """The header line and each body row as ``{header: cell}``, cut at the header's column starts."""
    lines = text.splitlines()
    at = next(i for i, line in enumerate(lines) if "SESSION" in line and "CHANGED" in line)
    header = lines[at]
    names = header.split()
    starts = [header.index(name) for name in names]
    bounds = list(zip(starts, [*starts[1:], None], strict=True))
    body = []
    for line in lines[at + 2 :]:
        if not line.strip():
            continue
        if not line.startswith(" ") and line.split()[0] not in KNOWN_FIRST_CELLS:
            break
        body.append({name: line[a:b].strip() for name, (a, b) in zip(names, bounds, strict=True)})
    return header, body


KNOWN_FIRST_CELLS = {LOCAL, "host-b", "host-c", "running", "relaunched", "unknown", "exited", "auto", "never"}


def _sessions(text):
    return [row["SESSION"] for row in _table(text)[1]]


def test_given_sessions_when_listed_then_one_aligned_table_has_headers_and_no_log_tags(host):
    out = _run("sessions", "list", "-a")
    header, body = _table(out)
    lines = [line for line in out.splitlines() if "c-myproject" in line]

    assert header.split() == ["HOST", "SESSION", "STATUS", "CAUSE", "CHANGED", "RELAUNCH"]
    assert not any("[sessions]" in line for line in lines)
    assert len(body) == 4
    assert {line.index("c-myproject") for line in lines} == {header.index("SESSION")}
    assert {line.index("2026-") for line in lines} == {header.index("CHANGED")}


def test_given_mixed_states_when_listed_with_all_then_active_rows_come_first_and_host_is_named_once(host):
    _, body = _table(_run("sessions", "list", "-a"))

    assert [row["SESSION"] for row in body] == [
        "c-myproject-ada",
        "c-myproject-bob",
        "c-myproject-amy",
        "c-myproject-eve",
    ]
    assert [row["STATUS"] for row in body] == ["running", "running", "unknown", "exited"]
    assert [row["HOST"] for row in body] == [LOCAL, "", "", ""]


def test_given_exited_sessions_when_bare_ai_sessions_runs_then_only_running_rows_print(host):
    bare = _run("sessions")
    listed = _run("sessions", "list")

    assert bare == listed
    assert "c-myproject-ada" in bare and "c-myproject-bob" in bare
    assert "c-myproject-eve" not in bare and "c-myproject-amy" not in bare
    assert "c-myproject-eve" in _run("sessions", "list", "--all")


def test_given_a_sort_flag_when_listed_then_it_overrides_the_config_order(host):
    cfg, _ = host
    cfg["sessions_list"] = {"sort_by": ["name"], "show_all": True}
    by_config = _sessions(_run("sessions", "list"))
    by_flag = _sessions(_run("sessions", "list", "-s", "changed"))

    assert by_config == ["c-myproject-ada", "c-myproject-amy", "c-myproject-bob", "c-myproject-eve"]
    assert by_flag == ["c-myproject-eve", "c-myproject-amy", "c-myproject-ada", "c-myproject-bob"]


def test_given_group_by_status_in_config_when_a_flag_says_none_then_the_flag_wins(host):
    cfg, _ = host
    cfg["sessions_list"] = {"group_by": "status", "show_all": True}
    header, body = _table(_run("sessions", "list"))
    assert header.split()[0] == "STATUS"
    assert [row["STATUS"] for row in body if row["STATUS"]] == ["running", "unknown", "exited"]

    header, _ = _table(_run("sessions", "list", "--group-by", "none"))
    assert header.split()[0] == "HOST"


@pytest.mark.parametrize(
    ("table", "args", "named"),
    [
        ({"group_by": "colour"}, [], "group_by"),
        ({"sort_by": ["size"]}, [], "sort_by"),
        ({"show_all": "yes"}, [], "show_all"),
        ({"order": "name"}, [], "order"),
        ({"pair_host": "host-b"}, [], "pair_host was renamed to group_host"),
        ({"scope": "pair"}, [], "scope must be one of"),
        ({}, ["-S", "pair"], "Invalid value"),
        ({}, ["-s", "name,size"], "-s/--sort-by"),
    ],
)
def test_given_an_invalid_view_setting_when_listed_then_it_is_refused_naming_the_setting(host, table, args, named):
    cfg, _ = host
    cfg["sessions_list"] = table
    result = CliRunner().invoke(_cli_group, ["sessions", "list", *args])

    assert result.exit_code == 2
    assert named in result.output


def test_given_a_fixed_registry_when_listed_as_json_then_records_keep_their_shape_and_order(host):
    cfg, _ = host
    cfg["sessions_list"] = {"show_all": False, "scope": "fleet", "sort_by": ["name"]}
    doc = json.loads(_run("sessions", "list", "-j"))

    assert doc == [
        {
            "name": "c-myproject-eve",
            "run_state": "exited",
            "cause": "manual_exit",
            "changed_at": "2026-01-03T00:00:00Z",
            "relaunch": "auto",
            "earlier_boot_gone": False,
        },
        {
            "name": "c-myproject-bob",
            "run_state": "running",
            "cause": "",
            "changed_at": "2026-01-01T00:00:00Z",
            "relaunch": "auto",
            "earlier_boot_gone": False,
        },
        {
            "name": "c-myproject-amy",
            "run_state": "unknown",
            "cause": "",
            "changed_at": "2026-01-02T03:04:05Z",
            "relaunch": "auto",
            "earlier_boot_gone": False,
        },
        {
            "name": "c-myproject-ada",
            "run_state": "running",
            "cause": "",
            "changed_at": "2026-01-02T00:00:00Z",
            "relaunch": "never",
            "earlier_boot_gone": False,
        },
    ]
    refused = CliRunner().invoke(_cli_group, ["sessions", "list", "-j", "-S", "fleet"])
    assert refused.exit_code == 2


def test_given_group_settings_when_listed_then_the_members_running_rows_follow_this_hosts(published):
    published["sessions_list"] = {"scope": "group", "group_host": "host-b"}
    out = _run("sessions", "list", "-S", "group")
    _, body = _table(out)

    assert [row["HOST"] for row in body if row["HOST"]] == [LOCAL, "host-b"]
    assert "c-webapp-kim" in out and "c-webapp-lou" not in out and "c-myapp-max" not in out
    assert "host-b: published snapshot from 2026-01-04T00:00:00Z" in out
    assert _run("sessions", "list") == out


def test_given_a_legacy_remote_alias_equal_to_the_host_id_when_listed_then_its_rows_appear(published):
    published["remote"] = {"default": "host-b", "machines": {"host-b": {"host": "192.0.2.10"}}}
    out = _run("sessions")

    assert "c-webapp-kim" in out and "c-myapp-max" not in out


def test_given_a_remote_alias_distinct_from_the_host_id_when_listed_then_the_id_selects_the_snapshot(published):
    published["remote"] = {"default": "server", "machines": {"server": {"host": "192.0.2.10", "host_id": "host-b"}}}

    assert "c-webapp-kim" in _run("sessions", "list")


def test_given_a_remote_group_member_without_an_outbound_target_when_listed_then_the_other_member_appears(published):
    published["machine"].update(host_id="host-b", session_group_members=[LOCAL, "host-b"])

    out = _run("sessions", "list")

    assert f"{LOCAL}: published snapshot from 2026-01-04T00:00:00Z" in out
    assert "no machine host group" not in out


@pytest.mark.parametrize("profile_class", ["personal", "work"])
def test_given_an_invalid_machine_scope_when_listed_then_it_is_refused(host, profile_class):
    cfg, _ = host
    cfg["machine"].update(profile_class=profile_class, sessions_list_scope="pair")
    result = CliRunner().invoke(_cli_group, ["sessions", "list"])

    assert result.exit_code == 2
    assert "[machine] sessions_list_scope must be one of" in result.output


def test_given_the_renamed_pair_host_key_when_listed_then_the_refusal_names_group_host_and_the_file(host):
    cfg, _ = host
    cfg["sessions_list"] = {"pair_host": "host-b", "group_by": "colour"}
    result = CliRunner().invoke(_cli_group, ["sessions", "list"])

    assert result.exit_code == 2
    assert "pair_host was renamed to group_host" in result.output
    assert 'rename the line to group_host = "host-b"' in result.output


@pytest.mark.parametrize(
    ("machine", "table", "args", "note"),
    [
        (
            {"profile_class": "work"},
            {"scope": "group", "group_host": "host-b"},
            [],
            'scope group narrowed to host: [machine] profile_class is "work", not "personal"; '
            'set [machine] profile_class = "personal" to list other hosts',
        ),
        (
            {},
            {},
            ["-S", "fleet"],
            'scope fleet narrowed to host: [machine] profile_class is unset, not "personal"; '
            'set [machine] profile_class = "personal" to list other hosts',
        ),
        (
            {"profile_class": "personal", "sessions_list_scope": "group"},
            {"scope": "fleet"},
            [],
            'scope fleet narrowed to group: [machine] sessions_list_scope is "group"; '
            'set [machine] sessions_list_scope = "fleet" to widen it',
        ),
    ],
)
def test_given_a_configured_scope_the_profile_narrows_when_listed_then_one_line_names_the_reason_and_key(
    published, machine, table, args, note
):
    published["machine"] = {"host_id": LOCAL, "session_group_members": [LOCAL, "host-b"]} | machine
    published["sessions_list"] = table

    out = _run("sessions", "list", *args)

    assert "c-myproject-ada" in out
    assert out.count(note) == 1


def test_given_a_host_only_profile_and_no_configured_scope_when_listed_then_no_narrowing_note_prints(host):
    cfg, _ = host
    cfg["machine"] = {"host_id": LOCAL, "profile_class": "work"}

    assert "narrowed" not in _run("sessions", "list")


def test_given_a_group_member_without_a_snapshot_when_listed_then_available_rows_and_a_publish_hint_appear(published):
    published["machine"]["session_group_members"] = [LOCAL, "host-b", "host-d", "host-e", "server"]
    published["remote"] = {"machines": {"server": {"host": "192.0.2.10"}}}

    out = _run("sessions", "list")

    assert "c-webapp-kim" in out
    assert "host-d: published snapshot from" in out
    assert "host-e: no readable session snapshot was published. Run `ai sessions publish` on host-e" in out
    assert "server: no snapshot matches this remote alias. Set [remote.machines.<alias>] host_id" in out


@pytest.mark.parametrize("profile_class", ["work", "client", ""])
def test_given_a_host_only_profile_when_fleet_is_requested_then_only_local_rows_appear(published, profile_class):
    published["machine"].update(profile_class=profile_class, session_group_members=[LOCAL, "host-b"])
    published["sessions_list"] = {"scope": "fleet"}
    if profile_class:
        published["machine"]["sessions_list_scope"] = "fleet"

    for args in ([], ["-S", "fleet"]):
        out = _run("sessions", "list", *args)
        assert "c-myproject-ada" in out
        assert "c-webapp-kim" not in out and "c-myapp-max" not in out
    repo = published["session_relaunch"]["publish"]["repo"]
    assert not _git(repo, "for-each-ref", "refs/session-state/")


def test_given_two_personal_group_members_when_each_lists_then_both_see_only_their_group(published, host):
    published["machine"]["session_group_members"] = [LOCAL, "host-b"]

    first = _run("sessions", "list", "-S", "group")
    published["machine"]["host_id"] = "host-b"
    _, write = host
    write(_record("c-webapp-kim", "running") | {"owning_host": "host-b"})
    second = _run("sessions", "list", "-S", "group")

    assert "c-myproject-bob" in first and "c-webapp-kim" in first
    assert "c-myproject-bob" in second and "c-webapp-kim" in second
    assert "c-myapp-max" not in first and "c-myapp-max" not in second
    assert "host-b: published snapshot from" in first and f"{LOCAL}: published snapshot from" in second


def test_given_a_personal_profile_when_its_scope_is_restricted_then_the_next_listing_applies_it(published):
    published["machine"]["session_group_members"] = [LOCAL, "host-b"]
    published["sessions_list"] = {"scope": "fleet", "group_host": "host-c"}

    default = _run("sessions", "list")
    assert "c-webapp-kim" in default and "c-myapp-max" not in default

    published["machine"]["sessions_list_scope"] = "host"
    restricted = _run("sessions", "list", "-S", "fleet")
    assert "c-myproject-ada" in restricted and "c-webapp-kim" not in restricted


def test_given_published_hosts_when_listed_with_fleet_scope_then_every_host_is_a_group(published):
    published["machine"]["sessions_list_scope"] = "fleet"
    out = _run("sessions", "list", "-S", "fleet", "-a")
    _, body = _table(out)

    assert [row["HOST"] for row in body if row["HOST"]] == [LOCAL, "host-b", "host-c"]
    assert "c-webapp-lou" in out and "c-myapp-max" in out


def test_given_no_publication_repo_when_listed_with_fleet_scope_then_this_host_shows_and_the_reason_prints(host):
    cfg, _ = host
    cfg["machine"]["sessions_list_scope"] = "fleet"
    out = _run("sessions", "list", "-S", "fleet")

    assert "c-myproject-ada" in out
    assert "no publication repo is configured" in out


def test_given_no_group_configured_when_bare_ai_sessions_runs_then_this_host_shows_with_config_guidance(host):
    out = _run("sessions")

    assert "c-myproject-ada" in out
    assert "Set [machine] session_group_members on each group host" in out


def test_given_no_running_sessions_when_listed_then_it_says_so_and_points_at_all(host):
    _, write = host
    write(_record("c-myproject-eve", "exited"))
    out = _run("sessions", "list", "-S", "host")

    assert "no running sessions recorded; -a/--all shows every row" in out


def test_given_the_shipped_defaults_when_compared_then_reference_template_and_built_in_values_agree():
    from ai_cli import config, session_list_view

    reference = tomllib.loads((REPO / "docs/reference/sessions-list-defaults.toml").read_text(encoding="utf-8"))
    template = tomllib.loads(config.DEFAULT_CONFIG)

    assert reference["sessions_list"] == session_list_view.DEFAULTS
    assert template["sessions_list"] == session_list_view.DEFAULTS
    assert session_list_view.DEFAULTS["group_by"] == "host"
    assert session_list_view.DEFAULTS["sort_by"][0] == "status"
