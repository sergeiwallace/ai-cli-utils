# Test setup failed parsing libtmux's new-session record on macOS

**Issue:** AI-CLI-5jcep. **Status:** fixed in the test helper; the macOS-side cause is narrowed, not proved.

## Symptom

On the macOS runner, two unrelated pull requests failed the same test in setup, before any code under test ran:

```text
tests/test_session_launch_integration.py  server.new_session(session_name=..., detach=True, window_command="sleep 600")
libtmux/server.py  new_session -> parse_output(session_stdout, "list-sessions", tmux_version)
libtmux/neo.py:657  dict(zip(formats, values, strict=True))
ValueError: zip() argument 2 is shorter than argument 1
```

The same run passed the test's other parameter, and main's macOS runs stayed green. A rerun of the failed job passed both times.

## What was measured

- libtmux 0.62.0 `Server.new_session` runs `new-session -P -F<format>`, where the format holds 125 session, window and pane fields for tmux 3.7. It splits stdout on newlines and parses only the first line. `zip(strict=True)` raises when that line holds fewer values than the format has fields.
- Each test gets its own socket in a fresh temporary directory, so a stray server on the runner cannot answer.
- That record is printed while the pane's child is still starting. Over 30 fresh servers on Linux, `pane_current_command` read `tmux`, `bash` or `sleep`, and `pane_current_path` was empty or the real path, depending on timing. Over 150 fresh servers on Linux the parse never failed.
- None of the measured values held a newline. So on macOS the first line was either cut short or split by a newline in a value. Both happen only on the command that starts the server, which is the only call that failed. Telling the two apart needs a macOS runner.

## Fix

The tests no longer use libtmux's `new_session` for setup. A helper runs `new-session -d` without `-P` and reads the session back by name, through the same session list the assertions already read on a running server. The regression test cuts the `-P` record short on purpose. libtmux then raises the same `ValueError` the runner showed, and the helper still creates the session.
