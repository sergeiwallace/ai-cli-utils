"""`ai iterm2 sessions --adopt`: record the live sessions that predate the registry."""

import json
import subprocess
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from conftest import run_cli

from ai_cli import session_registry
from ai_cli.config import ProjectPrefixError
from ai_cli.iterm2 import _PERSISTENCE_DEFAULTS

_USER = "someone"
_REMOTE_CONFIG = {
    "remote": {
        "default": "example-box",
        "machines": {
            "example-box": {"host": "192.0.2.10", "user": "exampleuser"},
            "other-box": {"host": "192.0.2.11", "user": "exampleuser"},
        },
    }
}
# Registered projects: prefix -> the name `-p` takes, and the prefix each name resolves to.
_PROJECTS = {"myp": "myproject", "web": "webapp"}
_PREFIX_OF = {"myproject": "MYP", "webapp": "web"}


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """Isolated config and state homes; returns (iterm2.toml writer, registry path)."""
    config_home = tmp_path / "config"
    state_home = tmp_path / "state"
    config_home.mkdir()
    monkeypatch.setattr("ai_cli.iterm2.get_xdg_config_home", lambda: config_home)
    monkeypatch.setattr("ai_cli.session_registry.get_xdg_state_home", lambda: state_home)
    toml = config_home / "iterm2.toml"
    toml.write_text("[iterm2]\nenabled = true\n", encoding="utf-8")
    return toml, state_home / session_registry.REGISTRY_FILENAME


def _proc(pid, cmdline, *, terminal=None, name="python3.12", user=_USER, cwd="/work/dir"):
    proc = MagicMock()
    proc.info = {
        "pid": pid,
        "name": name,
        "cmdline": cmdline,
        "terminal": terminal,
        "username": user,
        "create_time": 1000.0,
    }
    proc.cwd.return_value = cwd
    return proc


def _launcher(pid, *args, terminal="/dev/ttys021"):
    return _proc(pid, ["/tools/bin/python", "/home/bin/ai", "c", *args], terminal=terminal)


def _resolve(name):
    if name not in _PREFIX_OF:
        raise ProjectPrefixError(f"Unknown project {name!r}")
    return _PREFIX_OF[name]


@contextmanager
def _live(*, tmux_sessions=(), clients=None, procs=(), panes=None, platform="darwin"):
    """Stand in for tmux, the process table, the project registry and iTerm2's pane listing.

    ``clients``: tmux session -> attached client tty. ``panes``: tty -> (window, tab, pane).
    """
    clients = clients or {}
    panes = panes or {}
    calls: list[list[str]] = []

    def run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["tmux", "list-sessions"]:
            return subprocess.CompletedProcess(cmd, 0, "".join(f"{n}\n" for n in tmux_sessions), "")
        if cmd[:2] == ["tmux", "list-clients"]:
            tty = clients.get(cmd[3], "")
            return subprocess.CompletedProcess(cmd, 0, f"{tty}\n" if tty else "", "")
        if cmd[0] == "osascript":
            listing = "".join(f"{w}\t{t}\t{p}\t{tty}\tUUID-{tty[-2:]}\n" for tty, (w, t, p) in sorted(panes.items()))
            return subprocess.CompletedProcess(cmd, 0, listing, "")
        raise AssertionError(f"unexpected command {cmd}")

    me = MagicMock()
    me.username.return_value = _USER
    with (
        patch("ai_cli.session_registry.subprocess.run", side_effect=run),
        patch("psutil.process_iter", return_value=list(procs)),
        patch("psutil.Process", return_value=me),
        patch("ai_cli.config.registered_project_names", return_value=dict(_PROJECTS)),
        patch("ai_cli.config.get_project_aliases", return_value=dict(_PROJECTS)),
        patch("ai_cli.config.resolve_project_prefix_by_name", side_effect=_resolve),
        patch.object(sys, "platform", platform),
    ):
        yield calls


def _adopt(*extra):
    return run_cli(["ai", "iterm2", "sessions", "--adopt", *extra], config=_REMOTE_CONFIG)


def _records(path):
    return json.loads(path.read_text(encoding="utf-8"))["sessions"]


# --- local tmux sessions -------------------------------------------------------------


def test_given_live_ai_c_tmux_sessions_when_adopted_then_each_gets_a_local_record_with_its_relaunch_and_position(
    homes,
):
    _, registry = homes
    with _live(
        tmux_sessions=["c-myp-1", "c-myp-bootstrap-2", "main", "c-web-3"],
        clients={"c-myp-1": "/dev/ttys011", "c-web-3": "/dev/ttys099"},
        panes={"/dev/ttys011": (0, 2, 1)},
    ):
        code, out, err = _adopt()

    assert code == 0, err
    by_name = {r["name"]: r for r in _records(registry)}
    assert set(by_name) == {"c-myp-1", "c-myp-bootstrap-2", "c-web-3"}, "a plain shell session is not a candidate"
    assert by_name["c-myp-1"]["kind"] == "local"
    assert by_name["c-myp-1"]["relaunch_argv"] == ["ai", "c", "-p", "myproject", "1"]
    assert by_name["c-myp-bootstrap-2"]["relaunch_argv"] == ["ai", "c", "-p", "myproject", "bootstrap-2"]
    assert by_name["c-myp-1"]["iterm2"] == {
        "window": 0,
        "tab": 2,
        "pane": 1,
        "tty": "/dev/ttys011",
        "session_uuid": "UUID-11",
    }
    assert by_name["c-myp-bootstrap-2"]["iterm2"] is None, "a detached session has no position"
    assert by_name["c-web-3"]["iterm2"] is None, "a client tty iTerm2 does not show has no position"
    assert "main" not in out
    assert "adopted c-myp-1  local  window 0 tab 2 pane 1  relaunch: ai c -p myproject 1" in out
    assert out.strip().endswith("3 adopted, 0 already recorded, 0 skipped")


def test_given_a_tmux_name_no_registered_prefix_splits_when_adopted_then_it_is_skipped_with_the_reason(homes):
    _, registry = homes
    with _live(tmux_sessions=["c-unknown-1", "c-r-myp-1", "c-myp-notes"]):
        code, out, _ = _adopt()

    assert code == 0
    assert "skipped c-unknown-1 (no registered project prefix splits it into c-<prefix>-<n>)" in out
    assert "skipped c-r-myp-1 (c-r- names a session another machine launched here" in out
    assert "skipped c-myp-notes (no registered project prefix splits it" in out, "no slot index, no guess"
    assert not registry.exists()


def test_given_two_registered_prefixes_both_split_a_name_when_adopted_then_it_is_skipped_as_ambiguous(homes):
    _, registry = homes
    with (
        _live(tmux_sessions=["c-myp-web-1"]),
        patch("ai_cli.config.registered_project_names", return_value={"myp": "myproject", "myp-web": "mypweb"}),
    ):
        code, out, _ = _adopt()

    assert code == 0
    assert "skipped c-myp-web-1 (ambiguous: prefixes myp, myp-web all match)" in out
    assert not registry.exists()


# --- remote ai c -R launchers --------------------------------------------------------


def test_given_live_ai_c_remote_launchers_when_adopted_then_one_remote_record_per_launcher_not_per_ssh(homes):
    _, registry = homes
    procs = [
        _launcher(500, "2", "-R", "-p", "webapp", terminal="/dev/ttys021"),
        _launcher(501, "-R", "-m", "other-box", "-p", "myproject", "4", terminal="/dev/ttys022"),
        # The launcher's ssh child, and an unrelated ssh client: neither is a launcher.
        _proc(600, ["ssh", "-t", "exampleuser@192.0.2.10", "ai c --is-remote 2"], terminal="/dev/ttys021", name="ssh"),
        _proc(601, ["ssh", "-T", "vscode-remote"], name="ssh"),
        # A local `ai c` is not a remote launcher.
        _launcher(502, "1"),
    ]
    with _live(procs=procs, panes={"/dev/ttys021": (1, 0, 0)}):
        code, out, err = _adopt()

    assert code == 0, err
    records = _records(registry)
    assert [r["launcher_pid"] for r in records] == [500, 501]
    first, second = records
    assert first["kind"] == "remote"
    assert first["name"] == "c-r-web-2"
    assert first["remote"] == {"alias": "example-box", "session": "c-r-web-2"}, "the configured default alias"
    assert first["relaunch_argv"] == ["ai", "c", "2", "-R", "-p", "webapp"]
    assert first["cwd"] == "/work/dir"
    assert first["iterm2"]["tty"] == "/dev/ttys021"
    assert (first["iterm2"]["window"], first["iterm2"]["tab"]) == (1, 0)
    assert second["remote"] == {"alias": "other-box", "session": "c-r-myp-4"}, "the -m alias, prefix lowercased"
    assert second["iterm2"] is None
    assert "192.0.2" not in json.dumps(records) and "exampleuser" not in json.dumps(records)
    assert "adopted c-r-web-2  remote on example-box  pid 500  window 1 tab 0 pane 0" in out


def test_given_a_launcher_without_p_or_a_numeric_slot_when_adopted_then_remote_session_is_null_and_said_so(homes):
    _, registry = homes
    procs = [_launcher(700, "-R"), _launcher(701, "feature", "-R", "-p", "webapp")]
    with _live(procs=procs):
        code, out, _ = _adopt()

    assert code == 0
    records = {r["launcher_pid"]: r for r in _records(registry)}
    assert records[700]["remote"] == {"alias": "example-box", "session": None}
    assert records[700]["relaunch_argv"] == ["ai", "c", "-R"]
    assert records[701]["remote"]["session"] is None
    assert "adopted pid 700" in out
    assert "remote session unknown: no -p in the argv, so the remote prefix came from the launcher's directory" in out
    assert "remote session unknown: no numeric slot in the argv, so the remote host allocated it" in out


def test_given_unreadable_or_unparseable_launcher_argv_when_adopted_then_it_is_skipped_with_pid_and_reason(homes):
    _, registry = homes
    procs = [
        _proc(800, None, terminal="/dev/ttys030"),  # this user's python on a terminal, argv unreadable
        _proc(801, None, terminal="/dev/ttys031", user="root"),  # another user's: not a candidate
        _launcher(802, "-R", "-p"),  # -p with no value: the real parser rejects it
        _launcher(803, "1", "-R", "-m", "no-such-box"),
        _launcher(804, "-R", "-p", "nosuchproject", "2"),
    ]
    with _live(procs=procs):
        code, out, _ = _adopt()

    assert code == 0
    assert "skipped pid 800 (argv cannot be read)" in out
    assert "pid 801" not in out
    assert "skipped pid 802 (argv does not parse as ai c -R (Option '-p' requires an argument.))" in out
    assert "skipped pid 803 (remote machine does not resolve (" in out
    records = _records(registry)
    assert [r["launcher_pid"] for r in records] == [804]
    assert records[0]["remote"]["session"] is None, "an unresolvable -p never yields a guessed session name"
    assert "-p nosuchproject does not resolve" in out


# --- idempotence, dry run, config ----------------------------------------------------


def test_given_a_session_already_recorded_when_adopted_then_its_record_is_unchanged_and_reported(homes):
    _, registry = homes
    session_registry.record_launch(
        kind="local",
        name="c-myp-1",
        relaunch_argv=["ai", "c", "1"],
        cwd="/somewhere",
        remote=None,
        tty="",
        iterm_session_id=None,
        launcher_pid=1,
        config=_PERSISTENCE_DEFAULTS,
    )
    session_registry.record_launch(
        kind="remote",
        name="c-r-allocated-7",
        relaunch_argv=["ai", "c", "-R"],
        cwd=None,
        remote={"alias": "example-box", "session": "c-r-allocated-7"},
        tty="",
        iterm_session_id=None,
        launcher_pid=900,
        config=_PERSISTENCE_DEFAULTS,
    )
    before = registry.read_bytes()
    with _live(tmux_sessions=["c-myp-1"], procs=[_launcher(900, "-R")]):
        code, out, _ = _adopt()

    assert code == 0
    assert registry.read_bytes() == before
    assert "already recorded c-myp-1" in out
    assert "already recorded pid 900" in out, "the launch-time record of the same launcher process"
    assert out.strip().endswith("0 adopted, 2 already recorded, 0 skipped")


def test_given_a_record_from_an_earlier_process_with_the_same_pid_when_adopted_then_the_launcher_is_adopted(homes):
    _, registry = homes
    session_registry.record_launch(
        kind="remote",
        name="c-r-old-1",
        relaunch_argv=["ai", "c", "-R"],
        cwd=None,
        remote={"alias": "example-box", "session": "c-r-old-1"},
        tty="",
        iterm_session_id=None,
        launcher_pid=900,
        config=_PERSISTENCE_DEFAULTS,
    )
    reused = _launcher(900, "3", "-R", "-p", "webapp")
    reused.info["create_time"] = 4_000_000_000.0  # started after that record was written
    with _live(procs=[reused]):
        code, out, _ = _adopt()

    assert code == 0
    assert [r["name"] for r in _records(registry)] == ["c-r-old-1", "c-r-web-3"]


def test_given_dry_run_when_adopting_then_the_plan_is_printed_and_nothing_is_written(homes):
    _, registry = homes
    with _live(tmux_sessions=["c-myp-1"], procs=[_launcher(500, "2", "-R", "-p", "webapp")]):
        code, out, _ = _adopt("--dry-run")

    assert code == 0
    assert "would adopt c-myp-1  local" in out
    assert "would adopt c-r-web-2  remote on example-box" in out
    assert "2 to adopt, 0 already recorded, 0 skipped (dry run: registry not written)" in out
    assert not registry.parent.exists(), "a dry run creates neither the registry nor its directory"


@pytest.mark.parametrize(
    ("toml", "key"),
    [
        ("[iterm2.persistence.tracking]\nenabled = false\n", "[iterm2.persistence.tracking] enabled = false"),
        ("[iterm2.persistence]\nenabled = false\n", "[iterm2.persistence] enabled = false"),
    ],
)
def test_given_tracking_disabled_when_adopting_then_it_refuses_naming_the_key_and_writes_nothing(homes, toml, key):
    path, registry = homes
    path.write_text(toml, encoding="utf-8")
    with _live(tmux_sessions=["c-myp-1"], procs=[_launcher(500, "2", "-R", "-p", "webapp")]) as calls:
        code, _, err = _adopt()

    assert code == 1
    assert f"adopt refused: {key}" in err
    assert not registry.parent.exists()
    assert calls == [], "nothing is scanned once config refuses"


def test_given_tracking_filters_when_adopting_then_filtered_sessions_are_skipped_naming_the_rule(homes):
    path, registry = homes
    path.write_text('[iterm2.persistence.tracking]\ninclude_remote = false\nexclude = ["c-myp-2"]\n', encoding="utf-8")
    with _live(tmux_sessions=["c-myp-1", "c-myp-2"], procs=[_launcher(500, "2", "-R", "-p", "webapp")]):
        code, out, _ = _adopt()

    assert code == 0
    assert [r["name"] for r in _records(registry)] == ["c-myp-1"]
    assert "skipped c-myp-2 ([iterm2.persistence.tracking] exclude glob 'c-myp-2' matches)" in out
    assert "skipped c-r-web-2 ([iterm2.persistence.tracking] include_remote = false)" in out


def test_given_iterm2_does_not_answer_when_adopting_then_sessions_are_adopted_without_positions(homes):
    _, registry = homes
    with _live(tmux_sessions=["c-myp-1"], clients={"c-myp-1": "/dev/ttys011"}):
        real_run = session_registry.subprocess.run

        def timeout_osascript(cmd, *args, **kwargs):
            if cmd[0] == "osascript":
                raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
            return real_run(cmd, *args, **kwargs)

        with patch("ai_cli.session_registry.subprocess.run", side_effect=timeout_osascript):
            code, out, _ = _adopt()

    assert code == 0
    assert _records(registry)[0]["iterm2"] is None
    assert "positions not read (iTerm2 did not answer in 5s)" in out


def test_given_dry_run_without_adopt_when_invoked_then_it_is_a_usage_error(homes):
    code, _, err = run_cli(["ai", "iterm2", "sessions", "--dry-run"])

    assert code == 1
    assert "requires -a/--adopt" in err
