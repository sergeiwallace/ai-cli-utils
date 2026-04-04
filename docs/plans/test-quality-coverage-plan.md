# Test Quality Audit and Coverage Recovery — Implementation Plan

**Status:** DRAFT
**Created:** 2026-04-04
**Task:** `[AI-CLI-17]`

## Table of Contents

- [Overview](#overview)
- [Task Breakdown](#task-breakdown)
- [Batch Plan](#batch-plan)
- [Pragma Review](#pragma-review)
- [Human Gates](#human-gates)
- [Open Questions](#open-questions)
- [Approval Log](#approval-log)

## Overview

Comprehensive test suite quality audit and coverage recovery following the test_main.py split (AI-CLI-12 follow-up). Current coverage: 94% (down from 98%). Three categories of work: (1) fix test quality issues (vacuous assertions, over-mocking, real subprocess in unit tests), (2) fill coverage gaps across all modules, (3) remove lazily-added `# pragma: no cover` annotations from sync.py and replace with real tests. Target: ~100% coverage with no pragmas except `__main__` and unavoidable ImportError fallbacks.

> **Feedback Round 1:**
> - <enter feedback here>

## Task Breakdown

### T-01: Fix High-Severity Test Quality Issues

**Size:** S
**Batch:** 1

Fix tests that provide false confidence — vacuous assertions that pass even if the implementation does nothing.

**H1 — `test_quota.py` `test_when_usage_output_captured_then_returns_snapshot` (line 101)**

Current assertion: `assert result is None or isinstance(result, QuotaSnapshot)` — passes if the function returns `None`, i.e. the happy path is not verified at all.

Fix: Assert `isinstance(result, QuotaSnapshot)`, then assert the actual parsed values:
```python
assert isinstance(result, QuotaSnapshot)
assert result.session_pct == 12
assert result.week_all_pct == 86
assert result.week_sonnet_pct == 49
assert result.extra_enabled is False
```
Also fix the `fake_run` logic — the `if "% used" in cap_with_prompt.stdout` branch always returns `cap_with_prompt` either way (dead conditional). Replace with a call counter so the first capture-pane returns just `❯` and subsequent ones return the full usage output, matching the real scrape flow.

Add missing cleanup assertion on the timeout path: `test_when_cc_prompt_never_appears_then_returns_none` does not assert `kill-window` was called. Audit noted the exception test does check for `killed`. Add the same `killed` assertion to the timeout path.

**H2 — `test_iterm2.py` `test_when_all_slots_occupied_wraps_to_first` (line 204)**

Current assertion: `assert slot3 is not None` — doesn't verify wrapping behavior, passes if any non-None value is returned.

Fix: Assert slot3 wraps to the first palette color:
```python
assert slot3 == "e74c3c"  # wrapped back to first slot
```

**Deliverables:**
- Fix `test_quota.py`: `test_when_usage_output_captured_then_returns_snapshot` — real value assertions + fix dead conditional in `fake_run`
- Fix `test_quota.py`: add `killed` assertion to `test_when_cc_prompt_never_appears_then_returns_none`
- Fix `test_iterm2.py`: `test_when_all_slots_occupied_wraps_to_first` — assert specific wrapped color

**Acceptance criteria:**
- [ ] Happy-path quota snapshot test fails if `_scrape_usage_hidden_pane` returns `None`
- [ ] Happy-path quota snapshot test fails if parsed `session_pct` is wrong
- [ ] Slot-wrap test fails if wrapping returns second slot instead of first

**Dependencies:** None

---

### T-02: Fix Medium-Severity Test Issues

**Size:** S
**Batch:** 1

**M1 — `test_cli.py:161–206` publish tests: no behavioral assertion on what was published**

Four tests (`test_cli_when_internal_publish_event_then_instantiates_nats_client`, `test_cli_when_internal_publish_heartbeat_then_instantiates_nats_client`, `test_cli_when_internal_publish_session_event_then_instantiates_nats_client`, `test_cli_when_internal_publish_then_instantiates_nats_with_subject`) only assert `mock_nats.assert_called_once()`. They verify the NATSClient was instantiated but not that the correct subject/payload was dispatched.

Fix: After `mock_nats.assert_called_once()`, also assert on the publish call:
```python
mock_client = mock_nats.return_value
mock_client.publish.assert_called_once()
subject, payload = mock_client.publish.call_args[0]
assert subject == "session.event"   # or whichever subject applies
assert payload["session_id"] == "sess1"
```

**M2 — `test_handoff.py:40,84` — wrong patch target for NATSClient**

`patch("ai_cli.messaging.NATSClient")` patches at the source module. If `main.py` imports `from .messaging import NATSClient`, the local name in `main.py` is not affected by this patch. Works today only because the import happens to be re-looked-up at call time, but is brittle.

Fix: Change patch target to `"ai_cli.main.NATSClient"` (where it's used).

**M3 — `test_quota.py` — `_get_claude_usage_snapshot` has no direct tests**

`_get_claude_usage_snapshot()` is a thin wrapper but is the public entry point called by `quota_watch`. Currently tested only via `_scrape_usage_hidden_pane` indirectly. Add a direct test verifying it delegates to the scraper:
```python
def test_get_claude_usage_snapshot_when_scraper_returns_snapshot_then_returns_it():
    snap = QuotaSnapshot(session_pct=10, week_all_pct=50, week_sonnet_pct=30, extra_enabled=False)
    with patch("ai_cli.quota._scrape_usage_hidden_pane", return_value=snap):
        result = _get_claude_usage_snapshot()
    assert result is snap
```

**M4 — `test_session.py:115` — `test_build_session_name_with_index_when_called_then_respects_index` calls real subprocess**

`build_session_name("c", "sw", "3")` — passes a numeric string as name. Depending on the implementation path, this may or may not hit subprocess. Regardless, it should mock subprocess to ensure test isolation and CI safety:
```python
def test_build_session_name_with_index_when_called_then_respects_index():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        session_id, ai_name = build_session_name("c", "sw", "3")
    assert session_id == "c-sw-3"
    assert ai_name == "sw-3"
```

**M5 — `test_session.py:343` — real `time.sleep(0.01)` to get distinct mtimes**

`_time.sleep(0.01)` is used to ensure `new_file` has a later mtime than `old_file`. Filesystem mtime resolution is 1 second on some systems — this is a latent flakiness risk.

Fix: Mock `Path.stat` to return controlled mtime values instead of sleeping:
```python
import os, stat as stat_mod
mtimes = {str(old_file): 1000.0, str(new_file): 2000.0}

def fake_stat(self):
    r = os.stat_result((stat_mod.S_IFREG | 0o644, 0, 0, 0, 0, 0, 0, 0, mtimes.get(str(self), 0), 0))
    return r

with patch.object(Path, "stat", fake_stat):
    result = _find_latest_gemini_uuid("art-1")
```

**M6 — `test_messaging.py:346` — dead code in test body**

```python
with patch("ai_cli.messaging.NATSClient.__module__"):
    pass
```
This `with` block is empty (`pass`). The context manager enters and exits with no code running. It neither sets up state nor asserts anything. Delete it.

**Deliverables:**
- Fix `test_cli.py:161–206`: add subject/payload assertions to all 4 publish tests
- Fix `test_handoff.py:40,84`: change patch target to `ai_cli.main.NATSClient`
- Add `test_quota.py`: `test_get_claude_usage_snapshot_when_scraper_returns_snapshot_then_returns_it`
- Fix `test_session.py:115`: add subprocess mock
- Fix `test_session.py:343`: replace `time.sleep` with mocked `Path.stat`
- Fix `test_messaging.py:346`: remove dead `with` block

**Acceptance criteria:**
- [ ] Publish tests fail if wrong subject is dispatched
- [ ] Session index test passes in CI (no real subprocess)
- [ ] Latest-gemini-uuid test not sensitive to filesystem mtime resolution
- [ ] Dead code removed

**Dependencies:** None

---

### T-03: Remove sync.py Pragmas — Write Real Tests

**Size:** S
**Batch:** 1

All three `# pragma: no cover` annotations in sync.py were added lazily during the AI-CLI-12 coverage push. All three are mockable.

**sync.py:1293 — `except Exception` in `rglob` stat loop**

```python
except Exception:  # pragma: no cover
    pass
```
This fires if `memory_file.stat()` raises (e.g. file deleted between glob and stat — race condition guard). Testable by patching `Path.stat` to raise `PermissionError` for a specific file.

```python
def test_wait_for_dream_completion_when_stat_raises_then_continues(tmp_path, ...):
    # Create a memory file, then mock stat() to raise on it
    memory_file = ...
    with patch.object(Path, "stat", side_effect=PermissionError("no access")):
        # Should not raise; recent_write stays False
        _wait_for_dream_completion(verbose=False)
```

Remove `# pragma: no cover`.

**sync.py:1305 — `async def on_completed(data)`**

```python
async def on_completed(data):  # pragma: no cover
    completed.set()
```
This fires when a NATS message arrives on `memory.dream.completed`. Testable by mocking the NATS subscription to immediately invoke the callback:

```python
async def fake_subscribe(subject, cb):
    await cb(MagicMock())  # immediately fire the callback
    return AsyncMock()

with patch.object(mock_client.nc, "subscribe", side_effect=fake_subscribe):
    _wait_for_dream_completion(verbose=True)
```

Remove `# pragma: no cover`.

**sync.py:1324 — outermost `except Exception: pass`**

```python
except Exception:  # pragma: no cover
    pass
```
Fires if `asyncio.run(check())` raises. Testable by mocking `asyncio.run` to raise:

```python
def test_wait_for_dream_completion_when_asyncio_raises_then_nonfatal():
    with patch("asyncio.run", side_effect=RuntimeError("event loop broken")):
        _wait_for_dream_completion(verbose=False)  # must not raise
```

Remove `# pragma: no cover`.

**Deliverables:**
- `tests/test_sync.py`: 3 new tests targeting the 3 pragma locations
- `src/ai_cli/sync.py`: remove all 3 `# pragma: no cover` annotations

**Acceptance criteria:**
- [ ] stat-raises test covers line 1293
- [ ] callback-fired test covers line 1305–1306
- [ ] asyncio.run-raises test covers line 1324
- [ ] `sync.py` coverage reaches 100%

**Dependencies:** None

---

### T-04: Cover main.py Gaps

**Size:** M
**Batch:** 1

155 uncovered lines in main.py. Organized by function:

**`_migrate_xdg_dir` line 20 — rename branch**
```python
def test_migrate_xdg_dir_when_old_exists_and_new_does_not_then_renames(tmp_path):
    old = tmp_path / "old-name"
    old.mkdir()
    new = tmp_path / "new-name"
    result = _migrate_xdg_dir(old, new)
    assert not old.exists()
    assert new.exists()
    assert result == new
```

**`_load_iterm2_config` lines 721-722 — double-exception fallback**

Mock `tomllib.loads` to raise on both the file read and the default config parse:
```python
def test_load_iterm2_config_when_default_config_also_fails_then_returns_empty(tmp_path):
    with patch("ai_cli.main.get_xdg_config_home", return_value=tmp_path):
        with patch("tomllib.loads", side_effect=Exception("bad toml")):
            result = _load_iterm2_config()
    assert result == {}
```

**`_assign_iterm2_color_slot` lines 745, 747, 751 — disabled/empty cases**
```python
# iterm2.enabled = false
def test_assign_iterm2_color_slot_when_iterm2_disabled_then_returns_none(tmp_path):
    cfg = {"iterm2": {"enabled": False}}
    with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}):
        with patch("ai_cli.main._load_iterm2_config", return_value=cfg):
            assert _assign_iterm2_color_slot("sw-1", "c") is None

# iterm2.color.enabled = false
def test_assign_iterm2_color_slot_when_color_disabled_then_returns_none(tmp_path):
    cfg = {"iterm2": {"color": {"enabled": False}}}
    with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}):
        with patch("ai_cli.main._load_iterm2_config", return_value=cfg):
            assert _assign_iterm2_color_slot("sw-1", "c") is None

# empty palette
def test_assign_iterm2_color_slot_when_palette_empty_then_returns_none(tmp_path):
    cfg = {"iterm2": {"palette": {}}}
    with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}):
        with patch("ai_cli.main._load_iterm2_config", return_value=cfg):
            assert _assign_iterm2_color_slot("sw-1", "c") is None
```

**Lines 770-771 — corrupt lease file JSON**
```python
def test_assign_iterm2_color_slot_when_lease_file_corrupt_then_continues(tmp_path):
    cfg = _make_cfg(palette={"red": "#e74c3c"})
    lease_file = tmp_path / "color-leases.json"
    lease_file.write_text("not valid json {{{")
    with patch.dict(os.environ, {"LC_TERMINAL": "iTerm2"}):
        with patch("ai_cli.main._iterm2_state_dir", return_value=tmp_path):
            with patch("ai_cli.main._load_iterm2_config", return_value=cfg):
                result = _assign_iterm2_color_slot("sw-1", "c")
    assert result is not None  # recovered from corrupt file
```

**Lines 823-824 — additional lease/slot branch** *(read before implementing — exact lines TBD)*

**`_log_handoff_event` lines 1278-1279 — OSError on write**
```python
def test_log_handoff_event_when_write_fails_then_silent(tmp_path):
    with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
        with patch("builtins.open", side_effect=OSError("disk full")):
            _log_handoff_event("test.event", key="val")  # must not raise
```

**`_find_aicli_project_path` lines 1470-1471 — importlib exception**
```python
def test_find_aicli_project_path_when_importlib_raises_then_falls_back_to_cwd(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('name = "ai-cli-utils"\n')
    with patch("importlib.util.find_spec", side_effect=Exception("import error")):
        with patch("pathlib.Path.cwd", return_value=tmp_path):
            result = _find_aicli_project_path({})
    assert result == tmp_path
```

**`_auto_update_if_stale` line 1500 — failed update warning**
```python
def test_auto_update_if_stale_when_update_fails_then_prints_warning(tmp_path, capsys):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "ai-cli-utils"\nversion = "0.1.0"\n')
    stamp = get_xdg_state_home() / "last_update_commit.txt"
    # No stamp file or different hash → triggers update
    with patch("ai_cli.main._find_aicli_project_path", return_value=tmp_path):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="abc123\n"),  # git rev-parse
                MagicMock(returncode=1),                    # ai update --force fails
            ]
            _auto_update_if_stale({})
    assert "Warning: auto-update failed" in capsys.readouterr().err
```

**`_cmd_tunnel_start` lines 1553-1554 — no host configured**
```python
def test_cmd_tunnel_start_when_no_host_then_exits_1(capsys):
    with pytest.raises(SystemExit) as exc:
        _cmd_tunnel_start(4222, 4222, forward=False, config={})
    assert exc.value.code == 1
    assert "host not set" in capsys.readouterr().err
```

**`_ensure_nats_tunnel` lines 1594-1595, 1598-1599 — SystemExit from missing autossh**
```python
def test_ensure_nats_tunnel_when_autossh_missing_then_silent(tmp_path):
    config = {"messaging": {"tunnel_port": "4222"}}
    with patch("ai_cli.main._cmd_tunnel_start", side_effect=SystemExit(1)):
        with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
            _ensure_nats_tunnel(config)  # must not raise
```

**`_cmd_tunnel_stop` lines 1613-1614 — ProcessLookupError**
```python
def test_cmd_tunnel_stop_when_process_already_dead_then_cleans_pid_file(tmp_path):
    pid_file = tmp_path / "tunnel-4222.pid"
    pid_file.write_text("999999")
    with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
        with patch("os.kill", side_effect=ProcessLookupError):
            _cmd_tunnel_stop(4222)
    assert not pid_file.exists()
```

**`_cmd_tunnel_status` lines 1631-1633 — dead tunnel removed**
```python
def test_cmd_tunnel_status_when_pid_dead_then_removes_pid_file(tmp_path, capsys):
    pid_file = tmp_path / "tunnel-4222.pid"
    pid_file.write_text("999999")
    with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
        with patch("os.kill", side_effect=ProcessLookupError):
            _cmd_tunnel_status()
    assert not pid_file.exists()
    assert "dead" in capsys.readouterr().out
```

**`_ensure_circusd` lines 1682-1685 — circusd timeout RuntimeError**
```python
def test_ensure_circusd_when_daemon_never_ready_then_raises(tmp_path):
    with patch("ai_cli.main.get_xdg_state_home", return_value=tmp_path):
        with patch("circus.client.CircusClient") as mock_cc:
            mock_cc.return_value.send_message.side_effect = Exception("not ready")
            with patch("subprocess.Popen"):
                with patch("time.sleep"):
                    with pytest.raises(RuntimeError, match="circusd did not start"):
                        _ensure_circusd()
```

**Signal-watch NATS callback `except OSError` lines 1923-1924**
*(Tested in T-05 alongside other signal-watch handler coverage)*

**CLI dispatch branches (quota, tunnel, signal-watch, color, copier-update) lines 2067–2340**

These are all `sys.argv` dispatch tests — same pattern as existing `test_cli.py` tests:
```python
# quota status
def test_cli_when_quota_status_then_dispatches():
    with patch("sys.argv", ["ai", "quota", "status"]):
        with patch("ai_cli.main.load_config", return_value={}):
            with patch("ai_cli.quota.quota_status", return_value=0) as mock:
                with pytest.raises(SystemExit) as exc:
                    cli()
                mock.assert_called_once()
                assert exc.value.code == 0

# quota history, scrape, record, unknown subcommand — same pattern
# tunnel stop, status, unknown action
# signal-watch stop, status, unknown action
# color — no AI_TMUX_SESSION set
# color — unknown palette name
# color — hex value
# copier-update non-mac host
# copier-update --project filter
```

Lines 2617-2638 (project prefix branches in `cli()`):
```python
def test_cli_when_project_prefix_arg_set_then_uses_it():
    # --project-prefix passed directly
    ...

def test_cli_when_local_project_flag_then_derives_prefix():
    # -p flag without --remote
    ...
```

**Deliverables:**
- New tests in appropriate domain files covering all 155 uncovered lines in `main.py`

**Acceptance criteria:**
- [ ] `main.py` reaches 100% (or pragma-justified remainder)
- [ ] All new tests fail when their target function body is replaced with `pass`

**Dependencies:** None

---

### T-05: Cover Other Module Gaps

**Size:** M
**Batch:** 1

**`quota.py` line 141 — `quota_watch` PID already running branch**
```python
def test_quota_watch_when_already_running_then_returns_2(tmp_path):
    with patch("ai_cli.quota._acquire_pid_file", return_value=False):
        result = quota_watch()
    assert result == 2
```

**`quota.py` lines 223-224 — threshold-boundary publish**

The quota_watch threshold-crossing logic (e.g. 80% → publish alert). Requires mocking `_get_claude_usage_snapshot` and `NATSClient`:
```python
def test_quota_watch_when_threshold_crossed_then_publishes_alert():
    snap = QuotaSnapshot(session_pct=85, ...)
    with patch("ai_cli.quota._get_claude_usage_snapshot", return_value=snap):
        with patch("ai_cli.messaging.NATSClient") as mock_nats:
            ...
```

**`quota_db.py` lines 27-28, 44-46, 98, 114, 121 — DB error paths**

Each uncovered line is a `sqlite3` exception handler. Test by passing a corrupt DB path or mocking `sqlite3.connect` to raise:
```python
def test_init_db_when_path_unwritable_then_raises():
    with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("disk full")):
        with pytest.raises(sqlite3.OperationalError):
            init_db(Path("/bad/path/quota.db"))
```

**`gemini.py` lines 149-169 — `_load_doppler_env` with doppler found**
```python
def test_load_doppler_env_when_doppler_found_and_succeeds_then_sets_env_vars():
    env_output = "GOOGLE_API_KEY_FREE_TIER=abc123\nGOOGLE_API_KEY_TIER_1=xyz789\n"
    with patch("shutil.which", return_value="/usr/bin/doppler"):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=env_output)
            with patch.dict(os.environ, {}, clear=False):
                _load_doppler_env()
    assert os.environ.get("GOOGLE_API_KEY_FREE_TIER") == "abc123"

def test_load_doppler_env_when_doppler_fails_then_nonfatal():
    with patch("shutil.which", return_value="/usr/bin/doppler"):
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            _load_doppler_env()  # must not raise
```

**`icon_generator.py` line 78 — exception branch**
*(Read line 78 before implementing to determine exact mock needed)*

**`icon_generator.py` lines 233-234, 237-238 — branches**
*(Read before implementing)*

**`layout.py` lines 283-298 — `cmd_layout_apply`**
```python
def test_cmd_layout_apply_when_layout_found_then_runs_script(tmp_path):
    layout = _make_test_layout()
    with patch("ai_cli.layout.load_layout", return_value=layout):
        with patch("ai_cli.layout.generate_layout_profiles"):
            with patch("ai_cli.layout._write_launch_script", return_value=tmp_path / "script.py"):
                with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
                    result = cmd_layout_apply("test-layout")
    assert result == 0
    mock_run.assert_called_once()

def test_cmd_layout_apply_when_layout_not_found_then_returns_1(capsys):
    with patch("ai_cli.layout.load_layout", side_effect=FileNotFoundError("not found")):
        result = cmd_layout_apply("missing")
    assert result == 1
    assert "Error" in capsys.readouterr().err
```

**`layout.py` lines 350-399 — `_write_launch_script`**
```python
def test_write_launch_script_when_called_then_creates_runnable_python_file():
    layout = _make_test_layout(tabs=[...])
    path = _write_launch_script(layout)
    assert path.exists()
    content = path.read_text()
    assert "import iterm2" in content
    assert "async def main" in content
    assert "async_create" in content
    path.unlink()
```

**`copier_update.py` line 54 — exception branch**
*(Read line 54 before implementing)*

**`messaging.py` lines 58, 90 — error paths**
*(Read before implementing)*

**Signal-watch handler OSError lines 1923-1924 in `main.py`**

This is in the NATS message callback. Mock `Path.write_text` to raise on the pending-dir write:
```python
def test_signal_watch_handler_when_file_write_fails_then_continues():
    # Set up sw_handoff_dir, trigger the cross-machine payload path,
    # mock local_file.write_text to raise OSError
    # verify _claim_handoff_for_signal is still called
    ...
```

**Deliverables:**
- New tests in `test_quota.py`, `test_sync.py`, `test_gemini.py`, `test_layout.py`, `test_icon_generator.py`, `test_main.py`/domain files

**Acceptance criteria:**
- [ ] `quota.py` reaches 100%
- [ ] `quota_db.py` reaches 100%
- [ ] `gemini.py` reaches 100%
- [ ] `layout.py` reaches 100%
- [ ] `icon_generator.py` reaches 100%
- [ ] `copier_update.py` reaches 100%
- [ ] `messaging.py` reaches 100%

**Dependencies:** None

---

### T-06: Low-Severity Cleanup

**Size:** S
**Batch:** 1

**Merge `TestSshTunnel` and `TestOpenSshTunnel` in `test_messaging.py`**

Both test `_open_ssh_tunnel`. `TestSshTunnel::test_when_not_mac_then_no_tunnel_opened` and `TestOpenSshTunnel::test_skips_when_not_mac` are functionally identical. Merge into one class, remove the duplicate.

**Fix test naming inconsistency in `test_session.py`**

Flat function names like `test_build_session_name_no_name_when_no_sessions_then_uses_index_1` embed the subject redundantly. Rename to follow `test_{when}_{then}` pattern with subject in the class name, or `test_{subject}_{when}_{then}` consistently.

**Remove tautological dataclass assertions in `test_gemini.py::TestDataclasses`**

`assert r.attempts == []` and `assert r.success is False` test default field values in a dataclass. These provide zero behavioral signal. Remove or replace with a test that actually exercises construction logic.

**Deliverables:**
- `test_messaging.py`: merged class, removed duplicate
- `test_session.py`: renamed functions
- `test_gemini.py`: removed tautological assertions

**Acceptance criteria:**
- [ ] No duplicate test logic across messaging test classes
- [ ] All test names follow project convention
- [ ] Removed assertions were not the only coverage path for their target lines

**Dependencies:** None

---

## Batch Plan

| Batch | Tasks | Focus | Gate |
|-------|-------|-------|------|
| 1 | T-01–T-06 | All test quality + coverage | Human UAT |

Single batch — all tasks are independent test-only changes. No ordering dependency. Can run in parallel.

> **Feedback Round 1:** Does the batching make sense?
> - <enter feedback here>

## Pragma Review

### Existing Pragmas — All Recommended Keep

| Location | Code | Verdict | Reason |
|----------|------|---------|--------|
| `main.py:2866` | `if __name__ == "__main__":` | **Keep** | Universal — can never execute under pytest |
| `layout.py:24` | `except ImportError` for pydantic | **Keep** | Hard dep in pyproject.toml — can't fire in installed env |
| `icon_generator.py:23` | `except ImportError` for Pillow | **Keep** | Same — hard dep |
| `sync.py:1293` | `except Exception` in stat loop | **Remove** | T-03 adds real test |
| `sync.py:1305` | `async def on_completed` | **Remove** | T-03 adds real test |
| `sync.py:1324` | Outermost `except Exception` | **Remove** | T-03 adds real test |

### New Pragma Proposals — None

Both candidates identified earlier are mockable and get real tests in T-04:
- `main.py:1682-1685` (`circusd timeout RuntimeError`) — tested via `CircusClient.send_message` always raising
- `main.py:1923-1924` (`except OSError` in handoff file write) — tested via `Path.write_text` raising OSError

**No new pragmas proposed.**

## Human Gates

| Gate | After | Decision needed |
|------|-------|-----------------|
| Plan approval | Before coding | Confirm scope, approach, no-pragma stance |
| UAT | After Batch 1 | Run full suite, verify coverage report hits target |

## Open Questions

1. **Coverage target:** Is ~100% the right goal, or is 98%+ acceptable? Some of the CLI dispatch branches (e.g. `ai color` inner branches) involve deep iTerm2 escape sequences — they're testable but require significant mock scaffolding. Set a floor?

2. **T-05 reads:** Several T-05 items are marked "read before implementing" (icon_generator.py line 78, copier_update.py line 54, messaging.py lines 58/90). These are straightforward once read — flagging them here in case you want to approve those before implementation starts.

> **Feedback Round 1:**
> - <enter feedback here>

## Approval Log

| Date | Decision | Notes |
|------|----------|-------|
