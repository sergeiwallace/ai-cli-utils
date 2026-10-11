"""``ai iterm2 restore``: selection, the arrangement step and the AppleScript driver.

Every external program (osascript, tmux, ps, it2) is faked at ``subprocess.run``; nothing
here reaches a real iTerm2.
"""

import io
import json
import shlex
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import TEST_BOOT_ID, run_cli

from ai_cli import iterm2_restore

_IDLE = "/dev/ttys101"
_BUSY = "/dev/ttys102"
_SECOND_IDLE = "/dev/ttys103"


def _pane(window: int, tab: int, pane: int, uuid: str) -> dict:
    return {"window": window, "tab": tab, "pane": pane, "session_uuid": uuid}


def _record(
    name: str,
    *,
    kind: str = "local",
    refreshed: str = "2026-01-01T10:00:00Z",
    position=None,
    alias=None,
    boot_id=None,
    exit_cause=None,
    ended_at=None,
):
    number = name.rsplit("-", 1)[-1]
    argv = ["ai", "c", "-R", "-m", alias or "devbox", number] if kind == "remote" else ["ai", "c", number]
    extra: dict = {}
    if boot_id is not None:
        extra["boot_id"] = boot_id
    if exit_cause is not None:
        extra["exit"] = {"cause": exit_cause, "at": "2026-01-01T11:00:00Z", "evidence": {"source": "test"}}
    return extra | {
        "id": f"id-{name}",
        "kind": kind,
        "name": name,
        "relaunch_argv": argv,
        "cwd": "/work/myproject",
        "remote": {"alias": alias or "devbox", "session": name} if kind == "remote" else None,
        "iterm2": None if position is None else {**position, "tty": "/dev/ttys999"},
        "launched_at": "2026-01-01T09:00:00Z",
        "refreshed_at": refreshed,
        "launcher_pid": None,
        "ended_at": ended_at,
    }


class FakeSystem:
    """Answers osascript, tmux, ps and it2 the way a running iTerm2 machine would."""

    def __init__(self, *, panes=None, idle=(_IDLE, _SECOND_IDLE), attached=None, gone=(), it2_saved=("layout-a",)):
        self.panes = panes if panes is not None else {}
        self.idle = set(idle)
        self.attached = attached or {}
        self.gone = set(gone)
        self.it2_saved = list(it2_saved)
        self.timeout_on: str | None = None
        self.it2_hangs = False
        self.pane_answers: list[dict] | None = None
        self.calls: list[tuple[str, str]] = []

    def _osascript(self, script: str) -> tuple[str, str]:
        if "unique id of s" in script:
            panes = self.pane_answers.pop(0) if self.pane_answers else self.panes
            lines = [f"{p['window']}\t{p['tab']}\t{p['pane']}\t{tty}\t{p['session_uuid']}" for tty, p in panes.items()]
            return "list-panes", "\n".join(lines)
        if "create tab with default profile" in script:
            return "new-tab", "ok"
        if "write text" in script:
            return "write-pane", "ok"
        return "other", ""

    def __call__(self, cmd, *args, **kwargs):
        program = Path(cmd[0]).name
        stdout, returncode = "", 0
        if program == "osascript":
            kind, stdout = self._osascript(cmd[2])
            self.calls.append((kind, cmd[2]))
            if kind == self.timeout_on:
                raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))
        elif program == "tmux" and cmd[1] == "has-session":
            returncode = 1 if cmd[3].lstrip("=") in self.gone else 0
        elif program == "tmux" and cmd[1] == "list-clients":
            stdout = self.attached.get(cmd[3], "")
        elif program == "ps":
            rows = [f"{Path(tty).name} S+ -zsh" for tty in self.idle]
            rows += [f"{Path(_BUSY).name} Ss /usr/bin/login", f"{Path(_BUSY).name} S+ tmux", "?? Ss launchd"]
            stdout = "\n".join(rows)
        elif program == "it2":
            self.calls.append(("it2", " ".join(cmd[1:])))
            if self.it2_hangs:
                raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))
            if cmd[1:4] == ["window", "arrange", "list"]:
                stdout = "Saved arrangements:\n" + "".join(f"  - {name}\n" for name in self.it2_saved)
            elif cmd[1:4] == ["window", "arrange", "restore"]:
                returncode = 0 if cmd[4] in self.it2_saved else 1
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.calls]

    def typed(self) -> list[tuple[str, str]]:
        """(where, script) for each launch: "new-tab" or the tty a pane write targeted."""
        out = []
        for kind, script in self.calls:
            if kind == "new-tab":
                out.append(("new-tab", script))
            elif kind == "write-pane":
                tty = script.split('if tty of s is "', 1)[1].split('"', 1)[0]
                out.append((tty, script))
        return out


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A macOS machine with an isolated config and state home and a faked iTerm2."""
    state = tmp_path / "state"
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setattr("ai_cli.session_registry.get_xdg_state_home", lambda: state)
    monkeypatch.setattr("ai_cli.iterm2.get_xdg_config_home", lambda: config)
    monkeypatch.setattr(sys, "platform", "darwin")
    it2 = tmp_path / "it2"
    it2.write_text("", encoding="utf-8")
    monkeypatch.setattr(iterm2_restore, "IT2_PATH", it2)
    sleeps: list[float] = []
    monkeypatch.setattr(iterm2_restore.time, "sleep", sleeps.append)
    fake = FakeSystem()

    class Machine:
        system = fake
        slept = sleeps
        it2_path = it2
        registry = state / "iterm2-sessions.json"

        @staticmethod
        def config_text(text: str) -> None:
            (config / "iterm2.toml").write_text(text, encoding="utf-8")

        @staticmethod
        def config(restore_keys: str) -> None:
            Machine.config_text(f"[iterm2.persistence.restore]\nenabled = true\n{restore_keys}\n")

        @staticmethod
        def sessions(*records: dict) -> None:
            state.mkdir(parents=True, exist_ok=True)
            doc = {"schema": 1, "machine": "test", "sessions": list(records)}
            Machine.registry.write_text(json.dumps(doc), encoding="utf-8")

    # A context patch, not monkeypatch: monkeypatch unwinds after conftest's autouse
    # subprocess guard, and would put the guard's mock back once the guard has gone.
    with patch("ai_cli.iterm2.subprocess.run", fake):
        yield Machine


def _restore(*flags: str, stdin: str = ""):
    with patch("sys.stdin", io.StringIO(stdin)):
        return run_cli(["ai", "iterm2", "restore", *flags])


def _lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.strip()]


# --- T-3.0 it2 absent or refused ---------------------------------------------------


@pytest.mark.parametrize("unavailable", ["absent", "prompt-never-answered", "use_it2-false"])
def test_given_it2_unavailable_when_restoring_on_demand_then_sessions_restore_and_the_menu_path_is_printed(
    machine, unavailable
):
    machine.config(
        'default_arrangement = "layout-a"\n' + ("use_it2 = false\n" if unavailable == "use_it2-false" else "")
    )
    machine.sessions(_record("c-myproject-1"))
    if unavailable == "absent":
        machine.it2_path.unlink()
    elif unavailable == "prompt-never-answered":
        machine.system.it2_hangs = True

    code, out, _ = _restore()

    assert code == 0
    assert "arrangement: open it via Window > Restore Window Arrangement > layout-a" in out
    assert "c-myproject-1: restored (new tab)" in out
    assert [where for where, _ in machine.system.typed()] == ["new-tab"]
    if unavailable == "use_it2-false":
        assert "it2" not in machine.system.kinds()


# --- T-3.1 plan and selection ------------------------------------------------------


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        pytest.param(
            "[iterm2.persistence]\nenabled = false\n[iterm2.persistence.restore]\nenabled = true\n",
            "restore disabled by [iterm2.persistence] enabled=false",
            id="master-switch",
        ),
        pytest.param(
            "[iterm2.persistence.restore]\nenabled = false\n",
            "restore disabled by [iterm2.persistence.restore] enabled=false",
            id="restore-enabled",
        ),
        pytest.param(
            "[iterm2.persistence.restore]\nenabled = true\non_demand = false\n",
            "restore disabled by [iterm2.persistence.restore] on_demand=false",
            id="on-demand",
        ),
    ],
)
def test_given_restore_switched_off_when_run_on_demand_then_it_names_the_key_and_touches_nothing(
    machine, toml, message
):
    machine.config_text(toml)
    machine.sessions(_record("c-myproject-1"))
    before = machine.registry.read_bytes()

    code, out, _ = _restore()

    assert code == 0
    assert _lines(out) == [message]
    assert machine.system.calls == []
    assert machine.registry.read_bytes() == before


def test_given_on_startup_false_when_run_with_startup_then_it_exits_zero_silently(machine):
    machine.config("on_startup = false")
    machine.sessions(_record("c-myproject-1"))

    code, out, err = _restore("--startup")

    assert (code, out, err) == (0, "", "")
    assert machine.system.calls == []


def test_given_dry_run_when_restoring_then_the_ordered_plan_prints_and_neither_iterm2_nor_the_registry_changes(
    machine,
):
    machine.config('default_arrangement = "layout-a"')
    machine.system.panes = {_IDLE: _pane(0, 1, 0, "U-IDLE")}
    machine.system.gone = {"c-myproject-9", "c-myproject-8"}
    machine.sessions(
        _record("c-myproject-2", position=_pane(0, 2, 0, "U-OLD")),
        _record("c-myproject-1", position=_pane(0, 1, 0, "U-OLD-1")),
        _record("c-myproject-9", boot_id="boot-before"),
        _record("c-myproject-8", exit_cause="manual_exit"),
    )
    before = machine.registry.read_bytes()

    code, out, _ = _restore("--dry-run")

    assert code == 0
    assert _lines(out) == [
        "c-myproject-9: would record exit (cause=host_reboot; tmux session no longer exists)",
        "arrangement: would restore 'layout-a' with it2",
        "c-myproject-1: would restore (window 0 tab 1 pane 0): cd /work/myproject && ai c 1",
        "c-myproject-2: would restore (new tab): cd /work/myproject && ai c 2",
        "c-myproject-9: would restore (new tab): cd /work/myproject && AIH_LAUNCH_REASON=host_reboot ai c 9",
        "c-myproject-8: skipped (cause=manual_exit; restore.relaunch.scenarios.manual_exit = false)",
    ]
    assert "new-tab" not in machine.system.kinds()
    assert "write-pane" not in machine.system.kinds()
    assert "it2" not in machine.system.kinds()
    assert machine.registry.read_bytes() == before


def test_given_max_sessions_when_restoring_then_the_most_recently_refreshed_are_restored_and_the_rest_skipped(machine):
    machine.config("max_sessions = 2")
    machine.sessions(
        _record("c-myproject-1", refreshed="2026-01-01T08:00:00Z"),
        _record("c-myproject-2", refreshed="2026-01-01T11:00:00Z"),
        _record("c-myproject-3", refreshed="2026-01-01T10:00:00Z"),
    )

    code, out, _ = _restore()

    assert code == 0
    assert "c-myproject-2: restored (new tab)" in out
    assert "c-myproject-3: restored (new tab)" in out
    assert "c-myproject-1: skipped (max_sessions=2)" in out
    assert len(machine.system.typed()) == 2


def test_given_remote_hosts_when_a_remote_alias_is_not_listed_then_that_session_is_skipped_and_named(machine):
    machine.config('remote_hosts = ["devbox"]')
    machine.sessions(
        _record("c-r-myproject-1", kind="remote", alias="devbox"),
        _record("c-r-myproject-2", kind="remote", alias="otherbox"),
    )

    code, out, _ = _restore()

    assert code == 0
    assert "c-r-myproject-1: restored (new tab)" in out
    assert "c-r-myproject-2: skipped (remote_hosts does not list 'otherbox')" in out
    (only,) = machine.system.typed()
    assert "ai c -R -m devbox 1" in only[1]


@pytest.mark.parametrize(
    ("restore_keys", "flags", "skipped_line"),
    [
        pytest.param("include_local = false", (), "c-myproject-1: skipped (include_local=false)", id="include_local"),
        pytest.param('include = ["c-r-*"]', (), "c-myproject-1: skipped (include ['c-r-*'] has no matching glob)"),
        pytest.param('exclude = ["c-my*"]', (), "c-myproject-1: skipped (exclude glob 'c-my*' matches)", id="exclude"),
        pytest.param("", ("--only", "remote"), "c-myproject-1: skipped (--only remote)", id="only-remote"),
    ],
)
def test_given_a_selection_rule_when_restoring_then_a_filtered_session_is_skipped_with_its_rule(
    machine, restore_keys, flags, skipped_line
):
    machine.config(restore_keys)
    machine.sessions(_record("c-myproject-1"), _record("c-r-myproject-2", kind="remote"))

    code, out, _ = _restore(*flags)

    assert code == 0
    assert skipped_line in out
    assert "c-r-myproject-2: restored (new tab)" in out


def test_given_confirm_when_the_user_declines_then_neither_iterm2_nor_the_registry_changes(machine):
    machine.config("confirm = true")
    machine.system.gone = {"c-myproject-9"}
    machine.sessions(_record("c-myproject-1"), _record("c-myproject-9"))
    before = machine.registry.read_bytes()

    code, out, _ = _restore(stdin="n\n")

    assert code == 0
    assert "c-myproject-1: would restore (new tab): cd /work/myproject && ai c 1" in out
    assert "restore cancelled; nothing changed" in out
    assert machine.system.typed() == []
    assert machine.registry.read_bytes() == before


def test_given_confirm_when_the_user_accepts_then_dead_records_not_relaunched_are_pruned_and_sessions_restored(
    machine,
):
    machine.config("confirm = true")
    machine.system.gone = {"c-myproject-9"}
    machine.sessions(_record("c-myproject-1"), _record("c-myproject-9", exit_cause="manual_exit"))

    code, out, _ = _restore(stdin="y\n")

    assert code == 0
    assert "c-myproject-1: restored (new tab)" in out
    assert [r["name"] for r in json.loads(machine.registry.read_text(encoding="utf-8"))["sessions"]] == [
        "c-myproject-1"
    ]


def test_given_confirm_when_run_at_startup_then_it_never_asks(machine):
    machine.config("confirm = true\non_startup = true")
    machine.system.panes = {_BUSY: _pane(0, 0, 0, "U-BUSY")}
    machine.sessions(_record("c-myproject-1"))

    code, out, _ = _restore("--startup")

    assert code == 0
    assert "Restore 1 session(s)?" not in out
    assert "c-myproject-1: restored (new tab)" in out


def test_given_no_registry_when_restoring_then_it_says_no_sessions_are_recorded(machine):
    machine.config("")

    code, out, _ = _restore("--dry-run")

    assert code == 0
    assert f"sessions: none recorded in {machine.registry}" in out


# --- T-3.2 driver ------------------------------------------------------------------


def test_given_an_arrangement_on_demand_when_restoring_then_it2_restores_it_before_any_session_opens(machine):
    machine.config('default_arrangement = "layout-a"')
    machine.sessions(_record("c-myproject-1"))

    code, out, _ = _restore()

    assert code == 0
    assert "arrangement: restored 'layout-a' with it2" in out
    kinds = machine.system.kinds()
    restore_call = machine.system.calls.index(("it2", "window arrange restore layout-a"))
    assert restore_call < kinds.index("new-tab")
    assert restore_call < kinds.index("list-panes")


def test_given_the_arrangement_flag_when_restoring_then_it_overrides_default_arrangement(machine):
    machine.config('default_arrangement = "layout-a"')
    machine.system.it2_saved = ["layout-a", "layout-b"]
    machine.sessions(_record("c-myproject-1"))

    _restore("--arrangement", "layout-b")

    assert ("it2", "window arrange restore layout-b") in machine.system.calls
    assert ("it2", "window arrange restore layout-a") not in machine.system.calls


def test_given_startup_when_restoring_then_the_arrangement_is_not_reopened_and_its_idle_pane_is_filled(machine):
    machine.config('on_startup = true\ndefault_arrangement = "layout-a"')
    machine.system.panes = {_IDLE: _pane(0, 3, 0, "U-NEW")}
    machine.sessions(_record("c-myproject-1", position=_pane(0, 3, 0, "U-GONE")))

    code, out, _ = _restore("--startup")

    assert code == 0
    assert "it2" not in machine.system.kinds()
    assert "c-myproject-1: restored (window 0 tab 3 pane 0)" in out
    ((where, script),) = machine.system.typed()
    assert where == _IDLE
    assert 'write text "cd /work/myproject && ai c 1"' in script


def test_given_startup_when_iterm2_has_no_window_yet_then_it_waits_for_the_arrangement_then_fills(machine):
    machine.config("on_startup = true")
    machine.system.pane_answers = [{}, {}, {_IDLE: _pane(0, 0, 0, "U-NEW")}]
    machine.sessions(_record("c-myproject-1", position=_pane(0, 0, 0, "U-GONE")))

    code, out, _ = _restore("--startup")

    assert code == 0
    assert machine.system.kinds().count("list-panes") == 3
    assert "c-myproject-1: restored (window 0 tab 0 pane 0)" in out


def test_given_the_named_arrangement_does_not_exist_when_restoring_then_it_is_reported_and_sessions_continue(machine):
    machine.config('default_arrangement = "no-such-layout"')
    machine.sessions(_record("c-myproject-1"))

    code, out, _ = _restore()

    assert code == 0
    assert (
        "arrangement: 'no-such-layout' is not a saved arrangement (saved: 'layout-a'); restoring sessions only" in out
    )
    assert ("it2", "window arrange restore no-such-layout") not in machine.system.calls
    assert "c-myproject-1: restored (new tab)" in out


def test_given_recorded_panes_when_restoring_then_idle_ones_are_filled_busy_ones_overflow_and_launches_stagger(machine):
    machine.config("stagger_seconds = 2.5")
    machine.system.panes = {
        _IDLE: _pane(0, 0, 0, "U-A"),
        _BUSY: _pane(0, 1, 0, "U-B"),
        _SECOND_IDLE: _pane(1, 0, 0, "U-C"),
    }
    machine.sessions(
        _record("c-myproject-1", position=_pane(0, 0, 0, "U-A")),
        _record("c-myproject-2", position=_pane(0, 1, 0, "U-GONE")),
        _record("c-myproject-3", position=_pane(5, 5, 5, "U-C")),
        _record("c-myproject-4"),
    )

    code, out, _ = _restore()

    assert code == 0
    assert [where for where, _ in machine.system.typed()] == [_IDLE, "new-tab", _SECOND_IDLE, "new-tab"]
    assert "c-myproject-1: restored (window 0 tab 0 pane 0)" in out
    assert "c-myproject-2: restored (new tab)" in out
    assert "c-myproject-3: restored (window 1 tab 0 pane 0)" in out
    assert "c-myproject-4: restored (new tab)" in out
    assert machine.slept == [2.5, 2.5, 2.5]


def test_given_fill_arrangement_false_when_a_recorded_pane_is_idle_then_the_session_still_opens_in_a_new_tab(machine):
    machine.config("fill_arrangement = false")
    machine.system.panes = {_IDLE: _pane(0, 0, 0, "U-A")}
    machine.sessions(_record("c-myproject-1", position=_pane(0, 0, 0, "U-A")))

    _restore()

    assert [where for where, _ in machine.system.typed()] == ["new-tab"]


def test_given_a_session_already_attached_when_restoring_then_it_is_skipped_not_stolen(machine):
    machine.config("")
    machine.system.panes = {_BUSY: _pane(0, 4, 0, "U-B")}
    machine.system.attached = {"c-myproject-1": _BUSY}
    machine.sessions(_record("c-myproject-1"), _record("c-myproject-2"))

    code, out, _ = _restore()

    assert code == 0
    assert f"c-myproject-1: skipped (already open on {_BUSY} (window 0 tab 4 pane 0))" in out
    assert [where for where, _ in machine.system.typed()] == ["new-tab"]


@pytest.mark.parametrize("hung_call", ["new-tab", "write-pane"])
def test_given_iterm2_stops_answering_when_restoring_then_it_stops_reports_the_rest_and_leaves_the_registry(
    machine, hung_call
):
    machine.config("")
    machine.system.panes = {_IDLE: _pane(0, 0, 0, "U-A")}
    machine.system.timeout_on = hung_call
    first_position = _pane(0, 0, 0, "U-A") if hung_call == "write-pane" else None
    machine.sessions(_record("c-myproject-1", position=first_position), _record("c-myproject-2"))
    before = machine.registry.read_bytes()

    code, out, _ = _restore()

    assert code == 1
    assert "stopped: iTerm2 did not answer in 10s; the sessions below stay in the registry" in out
    assert "c-myproject-1: not restored (iTerm2 did not answer in 10s)" in out
    assert "c-myproject-2: not restored (iTerm2 did not answer in 10s)" in out
    assert not [line for line in _lines(out) if ": restored (" in line]
    assert machine.registry.read_bytes() == before


def test_given_the_pane_lookup_times_out_when_restoring_then_sessions_degrade_to_new_tabs(machine):
    machine.config("")
    machine.system.timeout_on = "list-panes"
    machine.sessions(_record("c-myproject-1", position=_pane(0, 0, 0, "U-A")))

    code, out, _ = _restore()

    assert code == 0
    assert "panes: not read (iTerm2 did not answer in 10s); sessions open in new tabs" in out
    assert "c-myproject-1: restored (new tab)" in out


def test_given_a_non_macos_platform_when_restoring_then_it_exits_zero_with_the_platform_message(machine, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    machine.config("")
    machine.sessions(_record("c-myproject-1"))

    code, out, _ = _restore()

    assert code == 0
    assert _lines(out) == ["restore is macOS/iTerm2 only"]
    assert machine.system.calls == []


# --- Exit causes, the relaunch filter and the handoff (AIH-zhqnf.4) ----------------


def _registry_records(machine) -> dict[str, dict]:
    return {r["name"]: r for r in json.loads(machine.registry.read_text(encoding="utf-8"))["sessions"]}


def _typed_text(script: str) -> str:
    return script.split('write text "', 1)[1].rsplit('"', 1)[0]


@pytest.mark.parametrize(
    ("record_boot_id", "cause"),
    [
        pytest.param(TEST_BOOT_ID, "terminal_quit_or_crash", id="same-boot"),
        pytest.param("boot-before-the-reboot", "host_reboot", id="boot-identity-differs"),
    ],
)
def test_given_a_dead_record_with_no_exit_when_restoring_then_the_sweep_records_its_cause_before_relaunching(
    machine, record_boot_id, cause
):
    machine.config("")
    machine.system.gone = {"c-myproject-9"}
    machine.sessions(_record("c-myproject-9", boot_id=record_boot_id))

    code, out, _ = _restore()

    assert code == 0
    assert f"c-myproject-9: exit recorded (cause={cause}; tmux session no longer exists)" in out
    assert f"c-myproject-9: restored (new tab; cause={cause})" in out
    recorded = _registry_records(machine)["c-myproject-9"]["exit"]
    assert recorded["cause"] == cause
    assert recorded["evidence"]["reason"] == "tmux session no longer exists"
    assert recorded["evidence"]["boot_id"] == TEST_BOOT_ID
    assert recorded["evidence"]["record_boot_id"] == record_boot_id
    ((where, script),) = machine.system.typed()
    assert where == "new-tab"
    assert _typed_text(script) == f"cd /work/myproject && AIH_LAUNCH_REASON={cause} ai c 9"


def test_given_a_relaunch_command_when_typed_then_aih_launch_reason_is_in_the_environment_of_the_ai_command(machine):
    """An assignment before ``cd`` would scope it to ``cd`` alone; it must prefix ``ai`` itself."""
    machine.config("")
    machine.system.gone = {"c-myproject-9"}
    machine.sessions(_record("c-myproject-9", boot_id="boot-before"))

    _restore()

    ((_, script),) = machine.system.typed()
    commands = [shlex.split(part) for part in _typed_text(script).split("&&")]
    assert commands[-1][:2] == ["AIH_LAUNCH_REASON=host_reboot", "ai"]


def test_given_a_dead_record_with_no_boot_identity_when_swept_then_it_is_unknown_and_follows_the_crash_toggle(
    machine,
):
    machine.config("[iterm2.persistence.restore.relaunch.scenarios]\nterminal_quit_or_crash = false")
    machine.system.gone = {"c-myproject-9"}
    machine.sessions(_record("c-myproject-9"))

    code, out, _ = _restore()

    assert code == 0
    assert "c-myproject-9: exit recorded (cause=unknown; tmux session no longer exists)" in out
    assert (
        "c-myproject-9: skipped (cause=unknown follows terminal_quit_or_crash; "
        "restore.relaunch.scenarios.terminal_quit_or_crash = false)"
    ) in out
    assert machine.system.typed() == []


@pytest.mark.parametrize(
    ("scenario", "record_kwargs"),
    [
        pytest.param("manual_exit", {"exit_cause": "manual_exit"}, id="manual_exit-by-default"),
        pytest.param("host_reboot", {"boot_id": "boot-before"}, id="host_reboot-switched-off"),
        pytest.param("terminal_quit_or_crash", {"boot_id": TEST_BOOT_ID}, id="crash-switched-off"),
    ],
)
def test_given_a_cause_whose_toggle_is_false_when_restoring_then_it_is_listed_skipped_with_its_cause_and_not_relaunched(
    machine, scenario, record_kwargs
):
    toggles = "" if scenario == "manual_exit" else f"{scenario} = false"
    machine.config(f"[iterm2.persistence.restore.relaunch.scenarios]\n{toggles}")
    machine.system.gone = {"c-myproject-9"}
    machine.sessions(_record("c-myproject-1"), _record("c-myproject-9", **record_kwargs))

    code, out, _ = _restore()

    assert code == 0
    assert f"c-myproject-9: skipped (cause={scenario}; restore.relaunch.scenarios.{scenario} = false)" in out
    assert [_typed_text(script) for _, script in machine.system.typed()] == ["cd /work/myproject && ai c 1"]
    assert list(_registry_records(machine)) == ["c-myproject-1"]


def test_given_manual_exit_switched_on_when_restoring_then_a_manually_exited_session_is_relaunched(machine):
    machine.config("[iterm2.persistence.restore.relaunch.scenarios]\nmanual_exit = true")
    machine.system.gone = {"c-myproject-9"}
    machine.sessions(_record("c-myproject-9", exit_cause="manual_exit"))

    code, out, _ = _restore()

    assert code == 0
    assert "c-myproject-9: restored (new tab; cause=manual_exit)" in out
    ((_, script),) = machine.system.typed()
    assert _typed_text(script) == "cd /work/myproject && AIH_LAUNCH_REASON=manual_exit ai c 9"


def test_given_a_cleanly_ended_remote_record_with_no_exit_when_swept_then_it_reads_as_a_manual_exit(machine):
    machine.config("")
    machine.sessions(_record("c-r-myproject-2", kind="remote", ended_at="2026-01-01T11:00:00Z"))

    code, out, _ = _restore()

    assert code == 0
    assert "c-r-myproject-2: exit recorded (cause=manual_exit; remote session exited cleanly)" in out
    assert "c-r-myproject-2: skipped (cause=manual_exit; restore.relaunch.scenarios.manual_exit = false)" in out
    assert machine.system.typed() == []


def test_given_a_live_record_when_restoring_then_no_exit_is_recorded_and_it_relaunches_without_a_reason(machine):
    machine.config("")
    machine.sessions(_record("c-myproject-1", boot_id="boot-before"))

    code, out, _ = _restore()

    assert code == 0
    assert "exit recorded" not in out
    assert "c-myproject-1: restored (new tab)" in out
    assert _registry_records(machine)["c-myproject-1"].get("exit") is None
    ((_, script),) = machine.system.typed()
    assert _typed_text(script) == "cd /work/myproject && ai c 1"


def test_given_a_live_session_carrying_a_stale_manual_exit_when_restoring_then_it_is_re_attached_not_filtered(machine):
    """The launcher relaunches claude after ``/exit`` in the same tmux session; that session still needs its pane."""
    machine.config("")
    machine.sessions(_record("c-myproject-1", exit_cause="manual_exit"))

    code, out, _ = _restore()

    assert code == 0
    assert "c-myproject-1: restored (new tab)" in out
    ((_, script),) = machine.system.typed()
    assert _typed_text(script) == "cd /work/myproject && ai c 1"


def test_given_on_terminal_reopen_false_when_restoring_at_startup_then_nothing_relaunches_and_the_reason_prints(
    machine,
):
    machine.config_text(
        "[iterm2.persistence.restore]\nenabled = true\non_startup = true\n"
        "[iterm2.persistence.restore.relaunch]\non_terminal_reopen = false\n"
    )
    machine.system.gone = {"c-myproject-9"}
    machine.sessions(_record("c-myproject-1"), _record("c-myproject-9", boot_id="boot-before"))

    code, out, _ = _restore("--startup")

    assert code == 0
    assert "relaunch disabled: restore.relaunch.on_terminal_reopen = false" in out
    assert machine.system.typed() == []
    assert "list-panes" not in machine.system.kinds()
    assert _registry_records(machine)["c-myproject-9"]["exit"]["cause"] == "host_reboot"


def test_given_on_terminal_reopen_false_when_restoring_on_demand_then_sessions_still_restore(machine):
    """The key governs the terminal-reopen (``--startup``) run; an on-demand run is the user asking."""
    machine.config_text(
        "[iterm2.persistence.restore]\nenabled = true\n"
        "[iterm2.persistence.restore.relaunch]\non_terminal_reopen = false\n"
    )
    machine.sessions(_record("c-myproject-1"))

    code, out, _ = _restore()

    assert code == 0
    assert "c-myproject-1: restored (new tab)" in out


def test_given_dry_run_when_the_sweep_would_classify_then_the_registry_is_not_written(machine):
    machine.config("")
    machine.system.gone = {"c-myproject-9"}
    machine.sessions(_record("c-myproject-9", boot_id="boot-before"))
    before = machine.registry.read_bytes()

    code, out, _ = _restore("--dry-run")

    assert code == 0
    assert "c-myproject-9: would record exit (cause=host_reboot; tmux session no longer exists)" in out
    assert machine.registry.read_bytes() == before
    assert machine.system.typed() == []
