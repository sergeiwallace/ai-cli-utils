"""Detecting a tmux that runs but cannot expand format strings.

Such a build answers ``-F '#{session_id}'`` with the literal ``#session_id``, which
makes session ownership impossible. These tests pin three things: the condition is
detected, it is NOT reported as a version mismatch, and the probe that establishes
it cannot hang on an inherited stdout pipe.
"""

from __future__ import annotations

import subprocess

import pytest

from ai_cli import tmux_setup

# --- the predicate -----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("#version", True),
        ("#session_id", True),
        ("#pid", True),
        ("3.7c", False),
        ("next-3.8", False),
        ("", False),
        (None, False),
    ],
)
def test_given_a_server_answer_when_checked_then_only_echoed_formats_look_unexpanded(
    value: str | None, expected: bool
) -> None:
    assert tmux_setup._looks_unexpanded(value) is expected


def test_given_a_server_that_echoes_formats_when_reported_then_formats_unexpanded_is_true() -> None:
    report = tmux_setup.TmuxReport(present=True, client_version="3.7c", server_version="#version")
    assert report.formats_unexpanded is True


def test_given_a_healthy_server_when_reported_then_formats_unexpanded_is_false() -> None:
    report = tmux_setup.TmuxReport(present=True, client_version="3.7c", server_version="3.7c")
    assert report.formats_unexpanded is False


# --- the guard that keeps this out of the version-mismatch path --------------


def test_given_a_server_echoing_formats_when_versions_compared_then_it_is_not_a_mismatch() -> None:
    """The load-bearing guard.

    ``#version`` differs from every real client version, so without the guard the
    launcher refuses with "client is 3.7c but the running server is #version" and
    tells the operator to restart the server -- which cannot help, because a freshly
    started server on such a build answers ``#version`` too.
    """
    report = tmux_setup.TmuxReport(present=True, client_version="3.7c", server_version="#version")
    assert report.versions_disagree is False


def test_given_genuinely_different_versions_when_compared_then_mismatch_still_fires() -> None:
    """Distinctness: the guard must not suppress a real mismatch."""
    report = tmux_setup.TmuxReport(present=True, client_version="3.7c", server_version="3.4")
    assert report.versions_disagree is True


def test_given_no_running_server_when_compared_then_not_a_mismatch() -> None:
    report = tmux_setup.TmuxReport(present=True, client_version="3.7c", server_version=None)
    assert report.versions_disagree is False


# --- what the operator is told ----------------------------------------------


def test_given_a_server_echoing_formats_when_lines_built_then_the_fault_is_named() -> None:
    report = tmux_setup.TmuxReport(present=True, path="/usr/bin/tmux", client_version="3.7c", server_version="#version")
    lines = tmux_setup.report_lines(report=report, bare=True, reason="formats do not expand")
    joined = "\n".join(lines)
    assert "does not expand format strings" in joined
    # It must NOT be presented as the server's version, which is what made the
    # condition read as a mismatch.
    assert "server reports #version" not in joined
    assert "WARNING" not in joined


# --- capability resolution ---------------------------------------------------


def _must_not_probe(**_kwargs) -> bool:
    pytest.fail("must not start a session when a running server already answered")


def test_given_a_running_server_that_echoes_formats_when_resolved_then_false(monkeypatch) -> None:
    monkeypatch.setattr(tmux_setup, "_probe_output", lambda argv, timeout: "#version")
    monkeypatch.setattr(tmux_setup, "formats_expand_probe", _must_not_probe)
    assert tmux_setup.formats_expand() is False


def test_given_a_running_healthy_server_when_resolved_then_true_without_creating_a_session(monkeypatch) -> None:
    monkeypatch.setattr(tmux_setup, "_probe_output", lambda argv, timeout: "3.7c")
    monkeypatch.setattr(tmux_setup, "formats_expand_probe", _must_not_probe)
    assert tmux_setup.formats_expand() is True


def test_given_no_running_server_when_probe_is_allowed_then_it_falls_back_to_the_throwaway_probe(
    monkeypatch,
) -> None:
    monkeypatch.setattr(tmux_setup, "_probe_output", lambda argv, timeout: None)
    monkeypatch.setattr(tmux_setup, "formats_expand_probe", lambda **_kwargs: False)
    assert tmux_setup.formats_expand(allow_probe=True) is False


def test_given_no_running_server_when_probe_is_not_allowed_then_nothing_is_created(monkeypatch) -> None:
    """The launch path must never create a tmux session just to answer this.

    A session-creating call in the preflight ran on every launch and was rejected
    by the suite's mocked tmux boundary -- the guard that exists to stop a test
    reaching a real tmux. ``None`` (undetermined) is the correct answer here, and
    callers must not degrade on it.
    """
    monkeypatch.setattr(tmux_setup, "_probe_output", lambda argv, timeout: None)
    monkeypatch.setattr(tmux_setup, "formats_expand_probe", _must_not_probe)
    assert tmux_setup.formats_expand() is None


# --- the throwaway probe ----------------------------------------------------


class _Recorder:
    """Stands in for subprocess.run, recording how stdout was wired."""

    def __init__(self, new_session_output: str) -> None:
        self.new_session_output = new_session_output
        self.calls: list[dict] = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": list(argv), **kwargs})
        if "new-session" in argv:
            handle = kwargs.get("stdout")
            if handle is not None and hasattr(handle, "write"):
                handle.write(self.new_session_output)
        return subprocess.CompletedProcess(argv, 0, None, None)


@pytest.mark.parametrize(
    ("output", "expected"),
    [("$4", True), ("#session_id", False), ("", None), ("garbage", None)],
)
def test_given_new_session_output_when_probed_then_capability_is_classified(output: str, expected: bool | None) -> None:
    recorder = _Recorder(output)
    assert tmux_setup.formats_expand_probe(runner=recorder) is expected


def test_given_a_probe_when_run_then_stdout_is_a_file_never_a_pipe() -> None:
    """Regression pin for an indefinite hang.

    ``new-session -d`` daemonizes a server that inherits the caller's stdout, so a
    captured PIPE is never closed and the call blocks forever -- measured running
    past 600s with a 30s timeout around it. Writing to a file removes the shared
    descriptor, so the probe must never ask for a pipe.
    """
    recorder = _Recorder("$1")
    tmux_setup.formats_expand_probe(runner=recorder)

    new_session = [c for c in recorder.calls if "new-session" in c["argv"]]
    assert new_session, "the probe must invoke new-session"
    for call in new_session:
        assert call.get("capture_output") is not True, "capture_output would reintroduce the pipe"
        assert call.get("stdout") is not subprocess.PIPE, "stdout must not be a pipe"
        assert hasattr(call.get("stdout"), "write"), "stdout must be a writable file object"
        assert call.get("stdin") is subprocess.DEVNULL


def test_given_a_probe_when_finished_then_its_session_is_killed_by_its_unique_name() -> None:
    recorder = _Recorder("#session_id")
    tmux_setup.formats_expand_probe(runner=recorder)

    created = next(c for c in recorder.calls if "new-session" in c["argv"])["argv"]
    name = created[created.index("-s") + 1]
    assert name.startswith("ai-cli-format-probe-"), "probe sessions must be identifiable"
    assert len(name) > len("ai-cli-format-probe-"), "the name needs a random suffix"

    killed = [c["argv"] for c in recorder.calls if "kill-session" in c["argv"]]
    assert killed, "the probe session must be cleaned up"
    assert killed[0] == ["tmux", "kill-session", "-t", name]


def test_given_a_probe_that_raises_when_run_then_cleanup_still_happens() -> None:
    """A failed new-session can still have created the session."""
    calls: list[list[str]] = []

    def flaky(argv, **kwargs):
        calls.append(list(argv))
        if "new-session" in argv:
            raise OSError("boom")
        return subprocess.CompletedProcess(argv, 0, None, None)

    assert tmux_setup.formats_expand_probe(runner=flaky) is None
    assert any("kill-session" in argv for argv in calls)
