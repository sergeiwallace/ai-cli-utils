"""The suite-wide guard against a test reaching the operator's desktop (AI-CLI-8n4h).

A guard never observed to fail enforces nothing, so every test here DRIVES the guard
rather than merely benefiting from it: each reintroduces the mistake and asserts the
guard catches it. The mistake is not hypothetical — AI-CLI-jk7v was one test of seven
siblings patching the conflict log while leaving the spawn live, which raised a real
macOS notification on every pytest run in this repo for weeks.

Every test is platform-independent on purpose. The production emitters branch on
``sys.platform``, so a test gated on the host OS would leave the other branches
unguarded on the very platforms where they are the live ones.
"""

import subprocess
import sys
from unittest.mock import patch

import pytest

from ai_cli import notifications, sync


class TestTheGuardFires:
    @pytest.mark.parametrize("program", ["osascript", "notify-send"])
    def test_given_a_direct_spawn_when_it_runs_then_it_is_refused_and_recorded(self, program, expect_desktop_escape):
        """Both emitters, not just the macOS one: ``notify-send`` is the other branch."""
        with pytest.raises(RuntimeError, match=r"reach the operator's desktop"):
            subprocess.run([program, "-e", 'display notification "x"'], capture_output=True, check=False)

        assert expect_desktop_escape == [program]

    def test_given_the_spawn_is_refused_when_reported_then_the_message_names_the_remedy(self, expect_desktop_escape):
        """The message has to say what to patch, or it just tells you that you failed."""
        with pytest.raises(RuntimeError) as raised:
            subprocess.run(["osascript", "-e", "beep"], capture_output=True, check=False)

        message = str(raised.value)
        assert "Patch subprocess.run" in message
        assert "not only the paths it writes" in message
        assert "AI-CLI-jk7v" in message
        assert expect_desktop_escape == ["osascript"]


class TestTheOriginalMistakeIsCaught:
    def test_given_only_the_conflict_log_is_patched_when_notifying_then_the_guard_catches_it(
        self, tmp_path, expect_desktop_escape
    ):
        """The exact shape of AI-CLI-jk7v, reintroduced.

        ``CONFLICT_LOG`` redirected, ``subprocess.run`` left alone. ``_is_mac`` is forced
        so the macOS branch is the one under test on every platform -- the guard
        intercepts before the real spawn, so it does not matter that ``osascript`` does
        not exist off macOS.
        """
        with (
            patch.object(sync, "CONFLICT_LOG", tmp_path / "conflicts.log"),
            patch.object(sync, "_is_mac", return_value=True),
        ):
            with pytest.raises(RuntimeError, match=r"reach the operator's desktop"):
                sync.notify_conflicts(["memory/MEMORY.md"])

        assert expect_desktop_escape == ["osascript"]

    def test_given_a_caller_that_swallows_exceptions_when_notifying_then_it_is_still_recorded(
        self, expect_desktop_escape
    ):
        """Why the record exists and the refusal alone is not enough.

        ``notifications._send_os_notification`` wraps its spawn in ``except Exception``
        and returns a failed result, so the guard's ``RuntimeError`` never reaches the
        test. Nothing is raised here -- deliberately -- and the attempt is still
        recorded, which is what the autouse teardown check turns into a failure.

        Runs on every platform rather than skipping Windows: since AI-CLI-e9nm the plyer
        branch is refused by a ``sys.modules`` stub, so all three branches now swallow a
        refusal and all three are recorded. The skip this used to carry was a real
        coverage loss on the one platform whose branch had no other guard.
        """
        expected = {"darwin": "osascript", "win32": "plyer"}.get(sys.platform, "notify-send")

        result = notifications._send_os_notification("title", "body")

        assert result.success is False, "the swallowed failure is the guard's refusal"
        assert expect_desktop_escape == [expected]

    def test_given_the_windows_toast_branch_when_notifying_then_the_import_guard_catches_it(
        self, expect_desktop_escape
    ):
        """The in-process branch the subprocess interception cannot see (AI-CLI-e9nm).

        Windows raises its toast by calling ``plyer.notification.notify`` in-process, so
        there is no spawn to refuse and the binary guard is structurally blind to it.
        ``sys.modules["plyer"]`` is shadowed for the whole session instead.

        Forced rather than skipped off Windows, matching every other test here: the
        branch is chosen by ``sys.platform``, so gating this on the host OS would leave
        it unguarded on the only platform where it is live.
        """
        with patch.object(notifications.sys, "platform", "win32"):
            result = notifications._send_os_notification("title", "body")

        assert result.success is False, "the swallowed failure is the stub's refusal"
        assert expect_desktop_escape == ["plyer"]

    def test_given_the_toast_is_refused_when_reported_then_the_message_names_the_right_boundary(
        self, expect_desktop_escape
    ):
        """Pointing the reader at a spawn boundary here would send them somewhere that
        does not exist, so the message has to name the import instead."""
        with pytest.raises(RuntimeError) as raised:
            sys.modules["plyer"].notification.notify(title="t", message="b")

        message = str(raised.value)
        assert "real plyer toast" in message
        assert "no subprocess" in message
        assert expect_desktop_escape == ["plyer"]

    def test_given_a_non_windows_platform_when_notifying_then_the_toast_stub_stays_silent(self, expect_desktop_escape):
        """Negative control for the import guard: it must record the plyer branch only.

        Without this, a stub that recorded on every call -- or a platform check that
        selected the Windows branch unconditionally -- would satisfy the test above
        while making every notification look like a toast.
        """
        with patch.object(notifications.sys, "platform", "darwin"):
            notifications._send_os_notification("title", "body")

        assert expect_desktop_escape == ["osascript"]


class TestTheGuardDoesNotBlockLegitimateTests:
    def test_given_subprocess_run_is_patched_when_notifying_then_nothing_is_recorded(self, tmp_path):
        """A test asserting a notification WOULD be sent must still be able to (AC-4).

        Deliberately does NOT request ``expect_desktop_escape``: this test relies on the
        autouse teardown check passing, so if patching at the boundary stopped being
        enough, this test fails rather than quietly opting out.
        """
        with (
            patch.object(sync, "CONFLICT_LOG", tmp_path / "conflicts.log"),
            patch.object(sync, "_is_mac", return_value=True),
            patch.object(sync.subprocess, "run") as run,
        ):
            sync.notify_conflicts(["memory/MEMORY.md"])

        assert run.call_count == 1
        assert run.call_args.args[0][0] == "osascript"

    def test_given_no_conflicts_when_notifying_then_no_spawn_is_even_attempted(self, tmp_path):
        """The AI-CLI-jk7v fix itself, still held: an empty list is a real no-op.

        Also the negative control for the guard -- a test that touches the notifier and
        records nothing proves the recorder is not simply always non-empty.
        """
        log = tmp_path / "conflicts.log"
        with patch.object(sync, "CONFLICT_LOG", log), patch.object(sync, "_is_mac", return_value=True):
            sync.notify_conflicts([])

        assert not log.exists()
