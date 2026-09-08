"""`ai c` must say what it decided about tmux, and why.

Before this, a launch resolved tmux silently: config opt-out, a missing binary, an
auto-install attempt and the bare-mode fallback all produced either nothing or a
notice that fired only on total failure. An operator could not tell whether the
session they were looking at was inside tmux at all, which version, or whether
something had just been installed on their machine.

The one non-obvious requirement, and the reason this is not simply `tmux -V`:
**the report must name the SERVER version separately from the client binary.** A
tmux server keeps its own version until every session on it exits, so during an
upgrade the two genuinely disagree — and consumers that parse tmux's output are
answered by the SERVER. A report that printed only `tmux -V` would state a
compatibility that does not hold. Measured on this fleet 2026-09-06: a NEWER
client against an OLDER live server, at which point the auto-compact transport was
refused by the server's version while the client looked usable.

Versions below are the two module constants ``OLDER_SERVER`` / ``NEWER_CLIENT``,
never literals. This file used to hard-code the exact pair measured that day; the
older of the two has since been removed from the fleet entirely, and what these
cases actually assert is that the probe separates two DIFFERENT versions -- not
anything about which two.
"""

from __future__ import annotations

import pytest

from ai_cli import tmux_setup

# Any two distinct version strings exercise this file. They are named rather than
# inlined so that a future fleet-wide version change is one edit here, and so no
# case can be read as a claim about a specific build.
OLDER_SERVER = "3.6"
NEWER_CLIENT = "3.7c"


def test_the_two_fixture_versions_really_differ() -> None:
    """Anti-vacuity control: every disagreement case below depends on this.

    If the two constants were ever set equal, `versions_disagree` would be False
    throughout and the whole file would pass while testing nothing.
    """
    assert OLDER_SERVER != NEWER_CLIENT


@pytest.fixture
def fake_tmux(monkeypatch):
    """Drive the probe's three inputs independently: PATH, client, server.

    Patches ``_probe_output`` -- the module's own one-command seam -- rather than
    the global ``subprocess.run``. The suite's autouse guard replaces
    ``subprocess.run`` to catch a test reaching a real tmux, and a second patch
    over the same global attribute restores in an order that can leave that guard
    installed past the test, which then breaks the session-scoped tmux cleanup at
    the very end of the run. ``_probe_output``'s own behaviour is covered
    separately below, against real non-protected processes.
    """

    state = {"which": "/usr/bin/tmux", "client": "3.7c", "server": "3.7c"}

    def _which(name):
        return state["which"] if name == "tmux" else None

    def _probe(argv, timeout):
        if argv[:2] == ["tmux", "-V"]:
            return None if state["client"] is None else f"tmux {state['client']}"
        if argv[:2] == ["tmux", "display-message"]:
            return state["server"]
        raise AssertionError(f"unexpected probe: {argv}")

    monkeypatch.setattr(tmux_setup.shutil, "which", _which)
    monkeypatch.setattr(tmux_setup, "_probe_output", _probe)
    return state


# --------------------------------------------------------------------------
# The one-command seam, against real processes that are not tmux.
# --------------------------------------------------------------------------


def test_probe_output_returns_stripped_stdout() -> None:
    assert tmux_setup._probe_output(["printf", "3.7c\n"], 10) == "3.7c"


def test_probe_output_returns_none_for_a_nonzero_exit() -> None:
    assert tmux_setup._probe_output(["false"], 10) is None


def test_probe_output_returns_none_for_a_missing_binary() -> None:
    """A real OSError, not a mocked one."""
    assert tmux_setup._probe_output(["ai-cli-no-such-binary-xyzzy"], 10) is None


def test_probe_output_returns_none_for_empty_output() -> None:
    """Empty is not an answer; treating "" as a version would print a blank one."""
    assert tmux_setup._probe_output(["true"], 10) is None


def test_probe_reports_path_client_and_server(fake_tmux) -> None:
    report = tmux_setup.probe()

    assert report.present is True
    assert report.path == "/usr/bin/tmux"
    assert report.client_version == NEWER_CLIENT
    assert report.server_version == "3.7c"


def test_probe_separates_client_from_server(fake_tmux) -> None:
    """The whole point: these are two processes and they disagree mid-upgrade."""
    fake_tmux["client"] = f"tmux {NEWER_CLIENT}"
    fake_tmux["server"] = OLDER_SERVER

    report = tmux_setup.probe()

    assert report.client_version == NEWER_CLIENT
    assert report.server_version == OLDER_SERVER
    assert report.versions_disagree is True


def test_no_running_server_is_not_a_disagreement(fake_tmux) -> None:
    """A fresh machine has no server; that is normal, not a mismatch."""
    fake_tmux["server"] = None

    report = tmux_setup.probe()

    assert report.server_version is None
    assert report.versions_disagree is False


def test_a_binary_that_cannot_run_is_present_but_versionless(fake_tmux) -> None:
    """Measured on a SageMaker space: on PATH, `tmux -V` exiting 127."""
    fake_tmux["client"] = None

    report = tmux_setup.probe()

    assert report.present is True
    assert report.runs is False
    assert report.client_version is None


def test_absent_tmux_probes_nothing(fake_tmux) -> None:
    fake_tmux["which"] = None

    report = tmux_setup.probe()

    assert report.present is False
    assert report.runs is False
    assert report.path is None


def test_probe_survives_a_binary_that_answers_nothing(fake_tmux) -> None:
    """A launch must never be blocked by a diagnostic.

    Both probes returning None is what a hung, timed-out or crashing tmux looks
    like to :func:`probe`, and ``_probe_output``'s own tests above prove those
    real failure modes do collapse to None.
    """
    fake_tmux["client"] = None
    fake_tmux["server"] = None

    report = tmux_setup.probe()

    assert report.present is True
    assert report.runs is False
    assert report.client_version is None
    assert report.server_version is None


# --------------------------------------------------------------------------
# The rendered block.
# --------------------------------------------------------------------------


def _lines(**kw) -> str:
    return "\n".join(tmux_setup.report_lines(**kw))


def test_report_states_the_mode_and_the_reason(fake_tmux) -> None:
    text = _lines(report=tmux_setup.probe(), bare=False, reason="default")

    assert "tmux" in text
    assert "3.7c" in text
    # The operator's actual question: is this session inside tmux?
    assert "inside tmux" in text


def test_report_names_bare_mode_and_why(fake_tmux) -> None:
    fake_tmux["which"] = None
    text = _lines(report=tmux_setup.probe(), bare=True, reason="tmux is not available here")

    assert "bare" in text
    assert "tmux is not available here" in text
    assert "inside tmux" not in text


def test_report_names_an_auto_install(fake_tmux) -> None:
    text = _lines(
        report=tmux_setup.probe(),
        bare=False,
        reason="default",
        auto_installed="conda",
    )

    assert "conda" in text
    assert "install" in text.lower()


def test_report_warns_when_client_and_server_disagree(fake_tmux) -> None:
    """Silence here is how a mixed install reads as a working one."""
    fake_tmux["server"] = OLDER_SERVER
    text = _lines(report=tmux_setup.probe(), bare=False, reason="default")

    assert OLDER_SERVER in text and NEWER_CLIENT in text
    assert "server" in text.lower()


def test_report_does_not_warn_when_they_agree(fake_tmux) -> None:
    text = _lines(report=tmux_setup.probe(), bare=False, reason="default")

    # Positive control first: without it this passes on an empty report.
    assert "3.7c" in text
    assert "disagree" not in text.lower()


# --------------------------------------------------------------------------
# The suite's own tmux guard: sharpened, not weakened.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["tmux", "new-session", "-d", "-s", "x"],
        ["tmux", "kill-server"],
        ["tmux", "attach", "-t", "x"],
        ["tmux", "send-keys", "-t", "x", "hi"],
        ["tmux"],
        "tmux new-session -d -s x",
    ],
)
def test_the_guard_still_rejects_a_session_creating_tmux(argv) -> None:
    from tests.conftest import _reject_real_agent_process

    with pytest.raises(RuntimeError, match="tmux"):
        _reject_real_agent_process(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["tmux", "-V"],
        ["tmux", "display-message", "-p", "#{version}"],
        "tmux -V",
    ],
)
def test_the_guard_permits_a_read_only_tmux_query(argv) -> None:
    """These create no server, no session and no window."""
    from tests.conftest import _reject_real_agent_process

    _reject_real_agent_process(argv)


def test_the_guard_rejects_every_other_protected_binary() -> None:
    """The read-only exemption is tmux-specific, not a general loosening."""
    from tests.conftest import _reject_real_agent_process

    for program in ("claude", "gemini", "direnv", "ssh", "mosh"):
        with pytest.raises(RuntimeError, match=program):
            _reject_real_agent_process([program, "-V"])


# --------------------------------------------------------------------------
# A bare launch must invoke tmux ZERO times.
# --------------------------------------------------------------------------


def test_a_presence_only_probe_spawns_nothing(monkeypatch) -> None:
    """bare mode has decided tmux is not involved; asking its version anyway is
    both a wasted spawn and a contract violation (tests/test_bare_worktree.py)."""
    monkeypatch.setattr(tmux_setup, "tmux_present", lambda: True)
    monkeypatch.setattr(tmux_setup.shutil, "which", lambda name: "/usr/bin/tmux")

    def _must_not_run(argv, timeout):
        raise AssertionError(f"a presence-only probe spawned {argv}")

    monkeypatch.setattr(tmux_setup, "_probe_output", _must_not_run)

    report = tmux_setup.probe(query_versions=False)

    assert report.present is True
    assert report.path == "/usr/bin/tmux"
    assert report.versions_probed is False
    assert report.client_version is None


def test_an_unprobed_report_does_not_claim_a_broken_binary(monkeypatch) -> None:
    monkeypatch.setattr(tmux_setup, "tmux_present", lambda: True)
    monkeypatch.setattr(tmux_setup.shutil, "which", lambda name: "/usr/bin/tmux")

    text = "\n".join(
        tmux_setup.report_lines(
            report=tmux_setup.probe(query_versions=False),
            bare=True,
            reason="--bare requested",
        )
    )

    assert "not queried" in text
    assert "does not run" not in text
    assert "--bare requested" in text
    assert "inside tmux" not in text


# --------------------------------------------------------------------------
# A mismatched client/server must REFUSE, not create an unattachable session.
# --------------------------------------------------------------------------


def test_the_refusal_message_names_both_versions_and_the_way_out() -> None:
    """The message has to carry the mechanism, because the failure is silent.

    Measured 2026-09-06: a NEWER client against an OLDER live server accepted
    `new-session -d` and then failed the attach with `open terminal failed: not
    a terminal`, leaving five sessions the operator could not enter and had to
    find and kill by hand. A warning next to a successful-looking launch is
    exactly the shape that produced that.
    """
    import inspect

    from ai_cli import main as ai_main

    source = inspect.getsource(ai_main._do_session_launch)
    assert "versions_disagree" in source, "the launch must consult the mismatch"
    # The refusal must EXIT, not warn: after the session exists the damage is done.
    guard = source[source.index("versions_disagree") :]
    assert "sys.exit(1)" in guard.split("\n\n")[0] or "sys.exit(1)" in guard[:2000]
    assert "open terminal failed" in guard, "name the symptom the operator saw"
    assert "LAST session" in guard, "explain why exiting all sessions is the fix"
    # Deliberately NOT "LAST session exits": the formatter may break a long
    # message across adjacent string literals, and an assertion that spans
    # such a break tests ruff's line wrapping rather than the wording.
    assert "--bare" in guard, "offer the escape hatch that needs no tmux at all"


def test_the_refusal_is_reached_only_through_the_disagreement(fake_tmux) -> None:
    """Agreement, and a bare launch, must both pass straight through."""
    fake_tmux["server"] = "3.7c"
    assert tmux_setup.probe().versions_disagree is False

    fake_tmux["server"] = None
    assert tmux_setup.probe().versions_disagree is False, (
        "no running server is the normal case and must never refuse a launch"
    )

    fake_tmux["server"] = OLDER_SERVER
    assert tmux_setup.probe(query_versions=False).versions_disagree is False, (
        "a bare launch does not probe, so it can never trip the guard"
    )
    assert tmux_setup.probe().versions_disagree is True, (
        "positive control: the mismatch this guard exists for IS detected"
    )
